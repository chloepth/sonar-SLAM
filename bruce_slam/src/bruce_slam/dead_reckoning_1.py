# python imports
import tf
import rospy
import gtsam
import numpy as np

# ros-python imports
from nav_msgs.msg import Odometry
from sensor_msgs.msg import PointCloud2, Imu
from message_filters import ApproximateTimeSynchronizer, Cache, Subscriber

# import custom messages
from kvh_gyro.msg import gyro as GyroMsg #error
from rti_dvl.msg import DVL #errro
from bar30_depth.msg import Depth #error

#bruce imports
from bruce_slam.utils.topics import *
from bruce_slam.utils.conversions import *
from bruce_slam.utils.io import *
from bruce_slam.utils.visualization import ros_colorline_trajectory

#----
#for Kalman filter
from scipy.linalg import sqrtm,expm,logm,norm,block_diag
from matplotlib.cbook import flatten
from numpy.linalg import inv
#----



class DeadReckoningNode(object):
	'''A class to support dead reckoning using DVL and IMU readings
	'''

	def __init__(self):

		self.pose = None #vehicle pose
		self.prev_time = None #previous reading time
		self.prev_vel = None #previous reading velocity
		self.keyframes = [] #keyframe list

		# Force yaw at origin to be aligned with x axis
		self.imu_yaw0 = None
		self.imu_pose = [0, 0, 0, -np.pi / 2, 0, 0]
		self.imu_rot = None
		self.dvl_max_velocity = 0.3

		# Create a new key pose when
		# - |ti - tj| > min_duration and
		# - |xi - xj| > max_translation or
		# - |ri - rj| > max_rotation
		self.keyframe_duration = None
		self.keyframe_translation = None
		self.keyframe_rotation = None

		self.dvl_error_timer = 0.0

		self.xhat = np.array([[0], [0], [0]])
		self.Gx = np.diag([0,0,0])

		self.c = False

		self.x = np.array([[0], [0], [0], [0], [0], [0], [0], [0], [0], [0], [0], [0]])


	def init_node(self, ns="~")->None:
		"""Init the node, fetch all paramaters from ROS

		Args:
			ns (str, optional): The namespace of the node. Defaults to "~".
		"""

		# Parameters for Node
		self.imu_pose = rospy.get_param(ns + "imu_pose")
		self.imu_pose = n2g(self.imu_pose, "Pose3")
		self.imu_rot = self.imu_pose.rotation()
		self.dvl_max_velocity = rospy.get_param(ns + "dvl_max_velocity")
		self.keyframe_duration = rospy.get_param(ns + "keyframe_duration")
		self.keyframe_translation = rospy.get_param(ns + "keyframe_translation")
		self.keyframe_rotation = rospy.get_param(ns + "keyframe_rotation")

		# Subscribers and caches
		self.dvl_sub = Subscriber(DVL_TOPIC, DVL)
		self.gyro_sub = Subscriber(GYRO_INTEGRATION_TOPIC, Odometry)
		self.depth_sub = Subscriber(DEPTH_TOPIC, Depth)
		self.depth_cache = Cache(self.depth_sub, 1)

		if rospy.get_param(ns + "imu_version") == 1:
			self.imu_sub = Subscriber(IMU_TOPIC, Imu)
		elif rospy.get_param(ns + "imu_version") == 2:
			self.imu_sub = Subscriber(IMU_TOPIC_MK_II, Imu)

		# Use point cloud for visualization
		self.traj_pub = rospy.Publisher(
			LOCALIZATION_TRAJ_TOPIC, PointCloud2, queue_size=10)

		self.odom_pub = rospy.Publisher(
			LOCALIZATION_ODOM_TOPIC, Odometry, queue_size=10)

		# are we using the FOG gyroscope?
		self.use_gyro = rospy.get_param(ns + "use_gyro")

		# define the callback, are we using the gyro or the VN100?
		if self.use_gyro:
			self.ts = ApproximateTimeSynchronizer([self.imu_sub, self.dvl_sub, self.gyro_sub], 300, .1)
			self.ts.registerCallback(self.callback_with_gyro)
		else:
			self.ts = ApproximateTimeSynchronizer([self.imu_sub, self.dvl_sub], 200, .1)
			self.ts.registerCallback(self.callback)

		self.tf = tf.TransformBroadcaster()

		loginfo("Localization node is initialized")

	def callback(self, imu_msg:Imu, dvl_msg:DVL)->None:
		"""Handle the dead reckoning using the VN100 and DVL only. Fuse and publish an odometry message.

		Args:
			imu_msg (Imu): the message from VN100
			dvl_msg (DVL): the message from the DVL
		"""

		#get the previous depth message
		depth_msg = self.depth_cache.getLast()

		#if there is no depth message, then skip this time step
		if depth_msg is None:
			return

		#check the delay between the depth message and the DVL
		dd_delay = (depth_msg.header.stamp - dvl_msg.header.stamp).to_sec()
		#print(dd_delay)
		if abs(dd_delay) > 1.0:
			logdebug("Missing depth message for {}".format(dd_delay))

		#convert the imu message from msg to gtsam rotation object
		rot = r2g(imu_msg.orientation)
		rot = rot.compose(self.imu_rot.inverse())

		#if we have no yaw yet, set this one as zero
		if self.imu_yaw0 is None:
			self.imu_yaw0 = rot.yaw()

		# Get a rotation matrix
		rot = gtsam.Rot3.Ypr(rot.yaw() - self.imu_yaw0, rot.pitch(), np.radians(90)+rot.roll())

		# parse the DVL message into an array of velocites
		vel = np.array([dvl_msg.velocity.x, dvl_msg.velocity.y, dvl_msg.velocity.z])

		# package the odom message and publish it
		self.send_odometry(vel,rot,dvl_msg.header.stamp,depth_msg.depth)

	def callback_with_gyro(self, imu_msg:Imu, dvl_msg:DVL, gyro_msg:GyroMsg)->None:
		"""Handle the dead reckoning state estimate using the fiber optic gyro. Here we use the
		Gyro as a means of getting the yaw estimate, roll and pitch are still VN100.

		Args:
			imu_msg (Imu): the vn100 imu message
			dvl_msg (DVL): the DVL message
			gyro_msg (GyroMsg): the euler angles from the gyro
		"""

		# decode the gyro message
		gyro_yaw = r2g(gyro_msg.pose.pose).rotation().yaw()

		#get the previous depth message
		depth_msg = self.depth_cache.getLast()

		#if there is no depth message, then skip this time step
		if depth_msg is None:
			return

		#check the delay between the depth message and the DVL
		dd_delay = (depth_msg.header.stamp - dvl_msg.header.stamp).to_sec()
		#print(dd_delay)
		if abs(dd_delay) > 1.0:
			logdebug("Missing depth message for {}".format(dd_delay))

		#convert the imu message from msg to gtsam rotation object
		rot = r2g(imu_msg.orientation)
		rot = rot.compose(self.imu_rot.inverse())

		#if we have no yaw yet, set this one as zero
		if self.imu_yaw0 is None:
			self.imu_yaw0 = rot.yaw()

		# Get a rotation matrix
		rot = gtsam.Rot3.Ypr(gyro_yaw, rot.pitch(), rot.roll())

		#parse the DVL message into an array of velocites
		vel = np.array([dvl_msg.velocity.x, dvl_msg.velocity.y, dvl_msg.velocity.z])

		# package the odom message and publish it
		self.send_odometry(vel,rot,dvl_msg.header.stamp,depth_msg.depth)



# Kalman filter -------

	def tolist(self,w): return list(flatten(w))

	def adjoint(self,w):
		if isinstance(w, (float, int)): return np.array([[0,-w] , [w,0]])
		w=self.tolist(w)
		return np.array([[0,-w[2],w[1]] , [w[2],0,-w[0]] , [-w[1],w[0],0]])

	def expw(self,w): return expm(self.adjoint(w))

	def eulermat(self,φ,θ,ψ):
		return self.expw(np.array([0,0,ψ])) @ self.expw([0,θ,0]) @ self.expw([φ,0,0])

	def predict(self, xhat, Gx, φ, θ, ψ, vr, dt):
		σv = 0 #standart deviation (m/s)
		Galpha = ((dt**2)*(σv**2))*np.eye(3,3)
		A = np.eye(3,3)
		y = np.eye(0,1)
		Gbeta = np.eye(0,0)
		n = len(xhat)
		C = np.eye(0,n)
		R = self.eulermat(φ,θ,ψ)
		vr = vr.reshape(3,1)
		u = dt*R@vr
		xhat, Gx = self.kalman(xhat,Gx,u,y,Galpha,Gbeta,A,C)
		#xhat, Gx = self.kalmanEKF(xhat,Gx,u,y,Galpha,Gbeta,A,C,φ, θ, ψ, vr,dt)
		return xhat,Gx

	def kalman_predict(self,xup,Gup,u,Γα,A):
	    Γ1 = A @ Gup @ A.T + Γα
	    x1 = A @ xup + u
	    return(x1,Γ1)

	def kalman_correc(self,x0,Γ0,y,Γβ,C):
	    S = C @ Γ0 @ C.T + Γβ
	    K = Γ0 @ C.T @ inv(S)
	    ytilde = y - C @ x0
	    Gup = (np.eye(len(x0))-K @ C) @ Γ0
	    xup = x0 + K@ytilde
	    return(xup,Gup)

	def kalman(self,x0,Γ0,u,y,Γα,Γβ,A,C):
		xup, Gup = self.kalman_correc(x0,Γ0,y,Γβ,C)
		x1, Γ1 = self.kalman_predict(xup,Gup,u,Γα,A)
		return(x1,Γ1)

#Extended Kalman Filter -----

	def f(self,φ,θ,ψ,vr):
		R = self.eulermat(φ,θ,ψ)
		return R @ vr

	def EKF(self,xup,Gup,u,Γα,A,φ,θ,ψ,vr,dt):
	    Γ1 = A @ Gup @ A.T + Γα
	    x1 = xup + self.f(φ,θ,ψ,vr)*dt
	    return(x1,Γ1)

	def kalmanEKF(self,x0,Γ0,u,y,Γα,Γβ,A,C,φ,θ,ψ,vr,dt):
	    xup, Gup = self.kalman_correc(x0,Γ0,y,Γβ,C)
	    x1, Γ1 = self.EKF(xup,Gup,u,Γα,A,φ,θ,ψ,vr,dt)
	    return(x1,Γ1)

#-------------

#Prediction

	def pred(self,x,dt):
		I = np.eye(12,12)
		At = np.diag(np.ones(6)*dt,6)
		A = I + At
		return A@x




	def send_odometry(self,vel:np.array,rot:gtsam.Rot3,dvl_time:rospy.Time,depth:float)->None:
		"""Package the odometry given all the DVL, rotation matrix, and depth

		Args:
			vel (np.array): a numpy array (1D) of the DVL velocities
			rot (gtsam.Rot3): the rotation matrix of the vehicle
			dvl_time (rospy.Time): the time stamp for the DVL message
			depth (float): vehicle depth
		"""

		#if the DVL message has any velocity above the max threhold do some error handling
		if np.any(np.abs(vel) > self.dvl_max_velocity):
			if self.pose:
				self.dvl_error_timer += (dvl_time - self.prev_time).to_sec()
				if self.dvl_error_timer > 5.0:
					logwarn(
						"DVL velocity ({:.1f}, {:.1f}, {:.1f}) exceeds max velocity {:.1f} for {:.1f} secs.".format(
							vel[0],
							vel[1],
							vel[2],
							self.dvl_max_velocity,
							self.dvl_error_timer,
						)
					)
				vel = self.prev_vel
			else:
				return
		else:
			self.dvl_error_timer = 0.0

		if self.pose:
			# figure out how far we moved in the body frame using the DVL message
			dt = (dvl_time - self.prev_time).to_sec()
			dv = (vel + self.prev_vel) * 0.5

			trans = dv * dt

#----
			if not self.c:
				#self.x = np.array([[0], [0], [0], [rot.roll()], [rot.pitch()], [rot.yaw()], [vel[0]], [vel[1]], [vel[2]], [0], [0], [0]])
				self.x = np.array([[0], [0], [0], [rot.roll()], [rot.pitch()], [rot.yaw()], [dv[0]], [dv[1]], [dv[2]], [0], [0], [0]])
				self.c = True

			else :
				self.x = self.pred(self.x,dt)

			print('trans',trans)
			print('self.x',self.x)
			#should I take dv or vel ? let's see


#-----
			#with kalman filter
			#self.xhat,self.Gx = self.predict(self.xhat,self.Gx,rot.roll(), rot.pitch(), rot.yaw(),dv,dt)
			#trans = self.xhat
			#trans = self.tolist(trans)

			# get a rotation matrix with only roll and pitch
			rotation_flat = gtsam.Rot3.Ypr(0, rot.pitch(), rot.roll())

			# transform our movement to the global frame
			# trans[2] = -trans[2]
			# trans = trans@(rotation_flat.matrix())

			# propagate our movement forward using the GTSAM utilities
			local_point = gtsam.Point2(trans[0], trans[1])

			#------
			#had to comment this :
			#
			# pose2 = gtsam.Pose2(
			# 	self.pose.x(), self.pose.y(), self.pose.rotation().yaw()
			# )
			# point = pose2.transformFrom(local_point)
			# self.pose = gtsam.Pose3(
			# 	rot, gtsam.Point3(point[0], point[1], depth)
			# )
			#--------

			self.pose = gtsam.Pose3(
			 	rot, gtsam.Point3(local_point[0], local_point[1], depth)
			 )

		else:
			# init the pose
			self.pose = gtsam.Pose3(rot, gtsam.Point3(0, 0, depth))

		# log the this timesteps messages for next time
		self.prev_time = dvl_time
		self.prev_vel = vel

		new_keyframe = False
		if not self.keyframes:
			new_keyframe = True
		else:
			duration = self.prev_time.to_sec() - self.keyframes[-1][0]
			if duration > self.keyframe_duration:
				odom = self.keyframes[-1][1].between(self.pose)
				odom = g2n(odom)
				translation = np.linalg.norm(odom[:3])
				rotation = abs(odom[-1])

				if (
					translation > self.keyframe_translation
					or rotation > self.keyframe_rotation
				):
					new_keyframe = True
		if new_keyframe:
			self.keyframes.append((self.prev_time.to_sec(), self.pose))
		self.publish_pose(new_keyframe)

	def publish_pose(self, publish_traj:bool=False)->None:
		"""Publish the pose

		Args:
			publish_traj (bool, optional): Are we publishing the whole set of keyframes?. Defaults to False.

		"""
		if self.pose is None:
			return

		header = rospy.Header()
		header.stamp = self.prev_time
		header.frame_id = "odom"

		odom_msg = Odometry()
		odom_msg.header = header
		# pose in odom frame
		odom_msg.pose.pose = g2r(self.pose)
		# twist in local frame
		odom_msg.child_frame_id = "base_link"
		# Local planer behaves worse
		# odom_msg.twist.twist.linear.x = self.prev_vel[0]
		# odom_msg.twist.twist.linear.y = self.prev_vel[1]
		# odom_msg.twist.twist.linear.z = self.prev_vel[2]
		# odom_msg.twist.twist.angular.x = self.prev_omega[0]
		# odom_msg.twist.twist.angular.y = self.prev_omega[1]
		# odom_msg.twist.twist.angular.z = self.prev_omega[2]
		odom_msg.twist.twist.linear.x = 0
		odom_msg.twist.twist.linear.y = 0
		odom_msg.twist.twist.linear.z = 0
		odom_msg.twist.twist.angular.x = 0
		odom_msg.twist.twist.angular.y = 0
		odom_msg.twist.twist.angular.z = 0
		self.odom_pub.publish(odom_msg)

		p = odom_msg.pose.pose.position
		q = odom_msg.pose.pose.orientation
		self.tf.sendTransform(
			(p.x, p.y, p.z), (q.x, q.y, q.z, q.w), header.stamp, "base_link", "odom"
		)

		if publish_traj:
			traj = np.array([g2n(pose) for _, pose in self.keyframes])
			traj_msg = ros_colorline_trajectory(traj)
			traj_msg.header = header
			self.traj_pub.publish(traj_msg)