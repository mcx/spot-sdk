<!--
Copyright (c) 2023 Boston Dynamics, Inc.  All rights reserved.

Downloading, reproducing, distributing or otherwise using the SDK Software
is subject to the terms and conditions of the Boston Dynamics Software
Development Kit License (20191101-BDSDK-SL).
-->

# Using the Robot State Service

This example program demonstrates how to query the robot state service for the hardware config, current robot state, robot metrics, joint states, or frame tree snapshot.

## Setup Dependencies

This example requires the bosdyn API and client to be installed, and must be run using python3. Using pip, these dependencies can be installed using:

```
python3 -m pip install -r requirements.txt
```

## Running the Example

To run the example:

```
python3 get_robot_state.py ROBOT_IP {state, hardware, metrics, joints, frame_tree}
```

As well, the program requires one of `{state, hardware, metrics, joints, frame_tree}` as a command line argument to specify which robot state request to issue when running the example program.
