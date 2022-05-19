# BlueRov SLAM 

Welcome! This repo contains the code for BlueROV sonar-based SLAM. This system uses a source of dead-reckoning, in our case a DVL and IMU with environment observations from an imaging sonar to perform graph-based pose slam using the ISAM2 backend implemented by gtsam. This method employs the scan matching paradigm to align scans using dead-reckoning as an initial guess. Point clouds for ICP are generated by converting sonar images to in-plane clouds. Note that this system is 3DOF and we assume fixed depth motion. 

This is the original work of Jinkun Wang. The documentation, maintenance, and active development are by John McConnell. 

# Sensor overview

Our vehicle is documented in this repo https://github.com/jake3991/Argonaut.git. The highlights are the following pieces of hardware. 
- Occulus M750d imaging sonar
- Occulus M1200 imaging sonar (optional)
- Rowe SeaPilot DVL
- Vectornav 100 MEMS IMU
- Bar30 pressure sensor
- KVH-DSP-1760 fiber optic gyroscope (optional)

# Python Dependencies, note python-3

```
cv_bridge
gtsam
matplotlib
message_filters
numpy
opencv_python
rosbag
rospy
scikit_learn
scipy
sensor_msgs
Shapely
tf
tqdm
```

# ROS Dependencies
```
ROS-noetic
catkin-pybind11
catkin-tools
```

# Installation
- Ensure all python dependencies are installed
- Check ros distro
- clone this repo into your catkin workspace
- clone git clone https://github.com/ethz-asl/libnabo.git into your catkin workspace
- clone https://github.com/ethz-asl/libpointmatcher.git into your catkin workspace
- clone https://github.com/jake3991/Argonaut.git into your catkin workspace
- clone https://github.com/borglab/gtsam.git into your catkin workspace
- build your workspace with catkin build NOT catkin_make

# Sample data
We provide a rosbag data file to test and run the SLAM system. Available here: https://drive.google.com/file/d/1s9hpmMoF-SJjWoM8EWz-t3mad6p15wbH/view?usp=sharing

# Running "Online"
This will launch the SLAM system, then we will playback the data as if it is happening now. 
- source catkin_ws/devel/setup.bash
- roslaunch bruce_slam slam.launch
- rosbag play your_data.bag

# Running Offline
This runs our offline mode, great for quick testing/tuning of parameters. 
- source catkin_ws/devel/setup.bash
- roslaunch bruce_slam slam.launch file:=path_to_data/your_data.bag

# Configuration
This SLAM system has many parameters, please read the wiki for an explanation of each parameter. However, we highly recommend using the default parameters in the config folder. If you are to tune anything it would be the feature extraction node in feature.yaml. 

# Current To Do list
- Add a gif to the readme to act as a nice landing page
- enhance some of the cpp documentation for CFAR

# Citation
If you use this repo please cite the following work. Link to pre-print here: https://arxiv.org/abs/2202.08359

```
@inproceedings{
  title={Virtual Maps for Autonomous Exploration of Cluttered Underwater Environments},
  author={Jinkun Wang, Fanfei Chen, Yewei Huang, John McConnell, Tixiao Shan, and Brendan Englot},
  booktitle={IEEE Journal of Oceanic Engineering,
  year={2022},
  organization={IEEE}
}
```






