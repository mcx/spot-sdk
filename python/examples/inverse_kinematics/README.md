<!--
Copyright (c) 2023 Boston Dynamics, Inc.  All rights reserved.

Downloading, reproducing, distributing or otherwise using the SDK Software
is subject to the terms and conditions of the Boston Dynamics Software
Development Kit License (20191101-BDSDK-SL).
-->

# Inverse Kinematics Service

API examples for how to interact with the [Inverse Kinematics Service](../../../docs/concepts/arm/arm_services.md#inverse-kinematics-service).

- reachability.py: Shows how to use the API to make reachability queries. The service will return a robot configuration (joint angles and body pose) that satisfies the requested reach or indicate that it did not find a solution. Also shows how to use the solution returned by the IK service in [robot commands](../../../docs/concepts/robot_services.md#robot-command).

## Understanding Spot Programming

For your best learning experience, please use the [Quickstart Guide](../../../docs/python/quickstart.md)
found in the SDK's docs/python directory. That will help you get your Python programming environment set up properly.

## Common Problems

1. Remember, you will need to launch a software e-stop separately. The E-Stop programming example is [here](../estop/README.md).
2. Make sure the Motor Enable button on the Spot rear panel is depressed.
3. Make sure Spot is sitting upright, with the battery compartment on the side closest the floor.

## Setup Dependencies

This example requires the bosdyn API and client to be installed, and must be run using python3. Using pip, these dependencies can be installed using:

```
python3 -m pip install -r requirements.txt
```

## Run the Example

To run the example:

```
python3 reachability.py ROBOT_IP
```
