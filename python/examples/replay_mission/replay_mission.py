# Copyright (c) 2023 Boston Dynamics, Inc.  All rights reserved.
#
# Downloading, reproducing, distributing or otherwise using the SDK Software
# is subject to the terms and conditions of the Boston Dynamics Software
# Development Kit License (20191101-BDSDK-SL).

"""
Mission Replay Script.  Command-line utility to replay stored missions, including Autowalk missions.
"""

import argparse
import os
import sys
import time

import google.protobuf.wrappers_pb2

import bosdyn.api.mission
import bosdyn.api.power_pb2 as PowerServiceProto
import bosdyn.client
import bosdyn.client.lease
import bosdyn.client.util
import bosdyn.geometry
import bosdyn.mission.client
import bosdyn.util
from bosdyn.api import robot_state_pb2
from bosdyn.api.autowalk import walks_pb2
from bosdyn.api.graph_nav import graph_nav_pb2, map_pb2, nav_pb2
from bosdyn.api.mission import mission_pb2, nodes_pb2
from bosdyn.client.autowalk import AutowalkResponseError
from bosdyn.client.power import PowerClient, power_on_motors, safe_power_off_motors
from bosdyn.client.robot_command import RobotCommandBuilder, RobotCommandClient, blocking_stand
from bosdyn.client.robot_state import RobotStateClient


def main():
    """Replay stored mission"""

    body_lease = None

    # Configure logging
    bosdyn.client.util.setup_logging()

    # Parse command-line arguments
    parser = argparse.ArgumentParser()

    bosdyn.client.util.add_base_arguments(parser)

    parser.add_argument('--upload_timeout', type=float, default=300.0, dest='upload_timeout',
                        help='Mission upload timeout.')
    parser.add_argument('--mission_timeout', type=float, default=3.0, dest='mission_timeout',
                        help='Mission client timeout.')
    parser.add_argument('--noloc', action='store_true', default=False, dest='noloc',
                        help='Skip initial localization')
    parser.add_argument('--disable_alternate_route_finding', action='store_true', default=False,
                        dest='disable_alternate_route_finding',
                        help='Disable creating alternate-route-finding graph structure')
    parser.add_argument('--disable_directed_exploration', action='store_true', default=False,
                        dest='disable_directed_exploration',
                        help='Disable directed exploration for skipped blocked branches')
    parser.add_argument('--strict_mode', action='store_true', default=False, dest='strict_mode',
                        help='Set strict path following mode')

    group = parser.add_mutually_exclusive_group()
    group.add_argument('--time', type=float, default=0.0, dest='duration',
                       help='Time to repeat mission (sec)')
    group.add_argument('--static', action='store_true', default=False, dest='static_mode',
                       help='Stand, but do not run robot')

    # Subparser for mission type
    subparsers = parser.add_subparsers(dest='mission_type', help='Mission type')
    subparsers.required = True

    # Subparser for simple mission
    simple_parser = subparsers.add_parser('simple', help='Simple mission (non-Autowalk)')
    simple_parser.add_argument('simple_mission_file', help='Mission file for non-Autowalk mission.')

    # Subparser for Autowalk mission
    autowalk_parser = subparsers.add_parser('autowalk', help='Autowalk mission using graph_nav')
    autowalk_parser.add_argument('--walk_directory', dest='walk_directory', required=True,
                                 help='Directory containing graph_nav map and autowalk missions')
    autowalk_parser.add_argument(
        '--walk_filename', dest='walk_filename', required=True, help=
        'Autowalk mission filename. Script assumes the path to this file is [walk_directory]/missions/[walk_filename]'
    )

    args = parser.parse_args()

    path_following_mode = map_pb2.Edge.Annotations.PATH_MODE_UNKNOWN

    # In strict mode, disable alternate waypoints and directed exploration
    if args.strict_mode:
        args.disable_alternate_route_finding = True
        args.disable_directed_exploration = True
        path_following_mode = map_pb2.Edge.Annotations.PATH_MODE_STRICT
        print('[ STRICT MODE ENABLED: Alternate waypoints and directed exploration disabled ]')

    if args.mission_type == 'simple':
        do_map_load = False
        fail_on_question = False
        do_localization = False
        mission_file = args.simple_mission_file
        walk_directory = None
        print(f'[ REPLAYING SIMPLE MISSION {mission_file} : HOSTNAME {args.hostname} ]')
    else:
        do_map_load = True
        fail_on_question = True
        if args.noloc:
            do_localization = False
        else:
            do_localization = True
        walk_directory = args.walk_directory
        mission_file = f'{walk_directory}/missions/{args.walk_filename}'

        print(
            f'[ REPLAYING AUTOWALK MISSION {mission_file} : WALK DIRECTORY {walk_directory} : HOSTNAME {args.hostname} ]'
        )

    # Initialize robot object
    robot = init_robot(args.hostname)
    if robot.is_estopped():
        robot.logger.fatal(
            'Robot is estopped. Please use an external E-Stop client, such as the estop SDK example, to configure E-Stop.'
        )
        sys.exit(1)

    # Check if mission_file exists.
    if not os.path.isfile(mission_file):
        robot.logger.fatal(f'Unable to find mission file: {mission_file}.')
        sys.exit(1)

    # Acquire robot lease
    robot.logger.info('Acquiring lease...')
    lease_client = robot.ensure_client(bosdyn.client.lease.LeaseClient.default_service_name)
    with bosdyn.client.lease.LeaseKeepAlive(lease_client, must_acquire=True, return_at_exit=True):
        # Initialize power client
        robot.logger.info('Starting power client...')
        power_client = robot.ensure_client(PowerClient.default_service_name)

        # Initialize other clients
        robot_state_client, command_client, mission_client, graph_nav_client = init_clients(
            robot, mission_file, walk_directory, do_map_load, args.disable_alternate_route_finding,
            args.upload_timeout)

        # Turn on power
        power_on_motors(power_client)

        # Stand up and wait for the perception system to stabilize
        robot.logger.info('Commanding robot to stand...')
        blocking_stand(command_client, timeout_sec=20)
        countdown(5)
        robot.logger.info('Robot standing.')

        # Localize robot
        localization_error = False
        if do_localization:
            graph = graph_nav_client.download_graph(timeout=args.upload_timeout)
            robot.logger.info('Localizing robot...')
            robot_state = robot_state_client.get_robot_state()
            localization = nav_pb2.Localization()

            # Attempt to localize using any visible fiducial
            graph_nav_client.set_localization(
                initial_guess_localization=localization, ko_tform_body=None, max_distance=None,
                max_yaw=None,
                fiducial_init=graph_nav_pb2.SetLocalizationRequest.FIDUCIAL_INIT_NEAREST)

        # Run mission
        if not args.static_mode and not localization_error:
            if args.duration == 0.0:
                run_mission(robot, mission_client, lease_client, fail_on_question,
                            args.mission_timeout, args.disable_directed_exploration,
                            path_following_mode)
            else:
                repeat_mission(robot, mission_client, lease_client, args.duration, fail_on_question,
                               args.mission_timeout, args.disable_directed_exploration,
                               path_following_mode)


def init_robot(hostname):
    """Initialize robot object"""

    # Initialize SDK
    sdk = bosdyn.client.create_standard_sdk('MissionReplay', [bosdyn.mission.client.MissionClient])

    # Create robot object
    robot = sdk.create_robot(hostname)

    # Authenticate with robot
    bosdyn.client.util.authenticate(robot)

    # Establish time sync with the robot
    robot.time_sync.wait_for_sync()

    return robot


def init_clients(robot, mission_file, walk_directory, do_map_load, disable_alternate_route_finding,
                 upload_timeout):
    """Initialize clients"""

    graph_nav_client = None

    # Create autowalk and mission client
    robot.logger.info('Creating mission client...')
    mission_client = robot.ensure_client(bosdyn.mission.client.MissionClient.default_service_name)
    robot.logger.info('Creating autowalk client...')
    autowalk_client = robot.ensure_client(
        bosdyn.client.autowalk.AutowalkClient.default_service_name)

    if do_map_load:
        if not os.path.isdir(walk_directory):
            robot.logger.fatal(f'Unable to find walk directory: {walk_directory}.')
            sys.exit(1)

        # Create graph-nav client
        robot.logger.info('Creating graph-nav client...')
        graph_nav_client = robot.ensure_client(
            bosdyn.client.graph_nav.GraphNavClient.default_service_name)

        # Clear map state and localization
        robot.logger.info('Clearing graph-nav state...')
        graph_nav_client.clear_graph()

        # Upload map to robot
        upload_graph_and_snapshots(robot, graph_nav_client, walk_directory,
                                   disable_alternate_route_finding, upload_timeout)

        # Here we assume the input file is an autowalk file so we parse it as a walk proto
        # and upload through the autowalk service.
        # If that fails we try parsing as a node and uploading through the mission service.
        try:
            upload_autowalk(robot, autowalk_client, mission_file, upload_timeout)
        except google.protobuf.message.DecodeError:
            robot.logger.warning(
                f'Failed to parse autowalk proto from {mission_file}. Attempting to parse as node proto.'
            )
            upload_mission(robot, mission_client, mission_file, upload_timeout)

    else:
        # Upload mission to robot
        upload_mission(robot, mission_client, mission_file, upload_timeout)

    # Create command client
    robot.logger.info('Creating command client...')
    command_client = robot.ensure_client(RobotCommandClient.default_service_name)

    # Create robot state client
    robot.logger.info('Creating robot state client...')
    robot_state_client = robot.ensure_client(RobotStateClient.default_service_name)

    return robot_state_client, command_client, mission_client, graph_nav_client


def countdown(length):
    """Print sleep countdown"""

    for i in range(length, 0, -1):
        print(i, end=' ', flush=True)
        time.sleep(1)
    print(0)


def upload_graph_and_snapshots(robot, client, path, disable_alternate_route_finding,
                               upload_timeout):
    """Upload the graph and snapshots to the robot"""

    # Load the graph from disk.
    graph_filename = os.path.join(path, 'graph')
    robot.logger.info(f'Loading graph from {graph_filename}')

    with open(graph_filename, 'rb') as graph_file:
        data = graph_file.read()
        current_graph = map_pb2.Graph()
        current_graph.ParseFromString(data)
        robot.logger.info(
            f'Loaded graph has {len(current_graph.waypoints)} waypoints and {len(current_graph.edges)} edges'
        )

    if disable_alternate_route_finding:
        for edge in current_graph.edges:
            edge.annotations.disable_alternate_route_finding = True

    # Load the waypoint snapshots from disk.
    current_waypoint_snapshots = dict()
    for waypoint in current_graph.waypoints:
        if len(waypoint.snapshot_id) == 0:
            continue
        snapshot_filename = os.path.join(path, 'waypoint_snapshots', waypoint.snapshot_id)
        robot.logger.info(f'Loading waypoint snapshot from {snapshot_filename}')

        with open(snapshot_filename, 'rb') as snapshot_file:
            waypoint_snapshot = map_pb2.WaypointSnapshot()
            waypoint_snapshot.ParseFromString(snapshot_file.read())
            current_waypoint_snapshots[waypoint_snapshot.id] = waypoint_snapshot

    # Load the edge snapshots from disk.
    current_edge_snapshots = dict()
    for edge in current_graph.edges:
        if len(edge.snapshot_id) == 0:
            continue
        snapshot_filename = os.path.join(path, 'edge_snapshots', edge.snapshot_id)
        robot.logger.info(f'Loading edge snapshot from {snapshot_filename}')

        with open(snapshot_filename, 'rb') as snapshot_file:
            edge_snapshot = map_pb2.EdgeSnapshot()
            edge_snapshot.ParseFromString(snapshot_file.read())
            current_edge_snapshots[edge_snapshot.id] = edge_snapshot

    # Upload the graph to the robot.
    robot.logger.info('Uploading the graph and snapshots to the robot...')
    true_if_empty = not len(current_graph.anchoring.anchors)
    response = client.upload_graph(graph=current_graph, generate_new_anchoring=true_if_empty,
                                   timeout=upload_timeout)
    robot.logger.info('Uploaded graph.')

    # Upload the snapshots to the robot.
    for snapshot_id in response.unknown_waypoint_snapshot_ids:
        waypoint_snapshot = current_waypoint_snapshots[snapshot_id]
        client.upload_waypoint_snapshot(waypoint_snapshot=waypoint_snapshot, timeout=upload_timeout)
        robot.logger.info(f'Uploaded {waypoint_snapshot.id}')

    for snapshot_id in response.unknown_edge_snapshot_ids:
        edge_snapshot = current_edge_snapshots[snapshot_id]
        client.upload_edge_snapshot(edge_snapshot=edge_snapshot, timeout=upload_timeout)
        robot.logger.info(f'Uploaded {edge_snapshot.id}')


def upload_autowalk(robot, autowalk_client, filename, upload_timeout):
    """Upload the autowalk mission to the robot"""

    # Load the autowalk from disk
    robot.logger.info(f'Loading autowalk from {filename}')

    autowalk_proto = walks_pb2.Walk()
    with open(filename, 'rb') as walk_file:
        data = walk_file.read()
        try:
            autowalk_proto.ParseFromString(data)
        except google.protobuf.message.DecodeError as exc:
            raise exc

    # Upload the mission to the robot and report the load_autowalk_response
    robot.logger.info('Uploading the autowalk to the robot...')
    try:
        load_autowalk_response = autowalk_client.load_autowalk(autowalk_proto,
                                                               timeout=upload_timeout)
    except AutowalkResponseError as resp_err:
        load_autowalk_response = resp_err.response
        print(f'failed_nodes:\n{load_autowalk_response.failed_nodes}')
        print(f'failed_elements: {load_autowalk_response.failed_elements}')
        raise resp_err


def upload_mission(robot, client, filename, upload_timeout):
    """Upload the mission to the robot"""

    # Load the mission from disk
    robot.logger.info(f'Loading mission from {filename}')

    mission_proto = nodes_pb2.Node()
    with open(filename, 'rb') as mission_file:
        data = mission_file.read()
        mission_proto.ParseFromString(data)

    # Upload the mission to the robot
    robot.logger.info('Uploading the mission to the robot...')
    client.load_mission(mission_proto, timeout=upload_timeout)
    robot.logger.info('Uploaded mission to robot.')


def run_mission(robot, mission_client, lease_client, fail_on_question, mission_timeout,
                disable_directed_exploration, path_following_mode):
    """Run mission once"""

    robot.logger.info('Running mission')

    mission_state = mission_client.get_state()

    while mission_state.status in (mission_pb2.State.STATUS_NONE, mission_pb2.State.STATUS_RUNNING):
        # We optionally fail if any questions are triggered. This often indicates a problem in
        # Autowalk missions.
        if mission_state.questions and fail_on_question:
            robot.logger.info(
                f'Mission failed by triggering operator question: {mission_state.questions}')
            return False

        body_lease = lease_client.lease_wallet.advance()
        local_pause_time = time.time() + mission_timeout

        play_settings = mission_pb2.PlaySettings(
            disable_directed_exploration=disable_directed_exploration,
            path_following_mode=path_following_mode)

        mission_client.play_mission(local_pause_time, [body_lease], play_settings)
        time.sleep(1)

        mission_state = mission_client.get_state()

    robot.logger.info(f'Mission status = {mission_state.Status.Name(mission_state.status)}')

    return mission_state.status in (mission_pb2.State.STATUS_SUCCESS,
                                    mission_pb2.State.STATUS_PAUSED)


def restart_mission(robot, mission_client, lease_client, mission_timeout):
    """Restart current mission"""

    robot.logger.info('Restarting mission')

    body_lease = lease_client.lease_wallet.advance()
    local_pause_time = time.time() + mission_timeout

    status = mission_client.restart_mission(local_pause_time, [body_lease])
    time.sleep(1)

    return status == mission_pb2.State.STATUS_SUCCESS


def repeat_mission(robot, mission_client, lease_client, total_time, fail_on_question,
                   mission_timeout, disable_directed_exploration, path_following_mode):
    """Repeat mission for period of time"""

    robot.logger.info(f'Repeating mission for {total_time} seconds.')

    # Run first mission
    start_time = time.time()
    mission_success = run_mission(robot, mission_client, lease_client, fail_on_question,
                                  mission_timeout, disable_directed_exploration,
                                  path_following_mode)
    elapsed_time = time.time() - start_time
    robot.logger.info(f'Elapsed time = {elapsed_time} (out of {total_time})')

    if not mission_success:
        robot.logger.info('Mission failed.')
        return False

    # Repeat mission until total time has expired
    while elapsed_time < total_time:
        restart_mission(robot, mission_client, lease_client, mission_timeout=3)
        mission_success = run_mission(robot, mission_client, lease_client, fail_on_question,
                                      mission_timeout, disable_directed_exploration,
                                      path_following_mode)

        elapsed_time = time.time() - start_time
        robot.logger.info(f'Elapsed time = {elapsed_time} (out of {total_time})')

        if not mission_success:
            robot.logger.info('Mission failed.')
            break

    return mission_success


if __name__ == '__main__':
    main()
