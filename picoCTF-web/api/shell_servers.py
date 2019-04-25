"""Module dealing with shell server integration."""
import json

import spur

import api.common
import api.config
import api.db
import api.problem
import api.team
from api.common import (
  InternalException,
  PicoException,
  safe_fail,
  validate,
  WebException
)


def get_server(sid):
    """
    Return the server dict corresponding to the sid provided.

    Args:
        sid: the server id to lookup

    Returns:
        The server dict, or None if the server was not found

    """
    db = api.db.get_conn()
    return db.shell_servers.find_one({"sid": sid}, {"_id": 0})


def get_connection(sid):
    """Attempt to connect to the given server and returns a connection."""
    server = get_server(sid)

    try:
        shell = spur.SshShell(
            hostname=server["host"],
            username=server["username"],
            password=server["password"],
            port=server["port"],
            missing_host_key=spur.ssh.MissingHostKey.accept,
            connect_timeout=10)
        shell.run(["echo", "connected"])
    except spur.ssh.ConnectionError:
        raise WebException(
            "Cannot connect to {}@{}:{} with the specified password".format(
                server["username"], server["host"], server["port"]))

    return shell


def ensure_setup(shell):
    """
    Verify that shell_manager is set up correctly.

    Leaves connection open.
    """
    result = shell.run(["sudo", "/picoCTF-env/bin/shell_manager", "status"],
                       allow_error=True)
    if (result.return_code == 1 and
            "command not found" in result.stderr_output.decode("utf-8")):
        raise WebException("shell_manager not installed on server.")


def add_server(
        *ignore,
        name,
        host,
        port,
        username,
        password,
        protocol,
        server_number
        ):
    """
    Add a shell server to the pool of servers.

    Servers are automatically assigned a server_number based on the current
    number of servers if not explicitly specified.

    Kwargs:
        name: display name
        host: hostname
        port: SSH port
        username
        password
        protocol: HTTP or HTTPS
        server_number
    Returns:
       sid of the newly created shell server
    Raises:
        PicoException: a shell server with this server_number already exists
    """
    db = api.db.get_conn()

    if not server_number:
        server_number = db.shell_servers.count() + 1

    if db.shell_servers.find_one(
            {'server_number': server_number}) is not None:
        raise PicoException('Shell server with this server_number ' +
                            'already exists.', status_code=409)

    sid = api.common.token()
    db.shell_servers.insert_one({
        'sid': sid,
        'name': name,
        'port': port,
        'username': username,
        'password': password,
        'protocol': protocol,
        'server_number': server_number
    })
    return sid


def update_server(sid, updates):
    """
    Update a shell server.

    Args:
        sid: the sid of the server to update
        updates: dict of updated shell server fields

    Returns:
        sid of the updated server (unchanged), or
        None if the provided sid was not found

    Raises:
        PicoException if attempting to set server_number to one already
        in use by a different server

    """
    db = api.db.get_conn()

    # Make sure we are not duplicating a server number
    if 'server_number' in updates and db.shell_servers.find_one(
            {
                'server_number': updates['server_number'],
                'sid': {'$ne': sid}
            }):
        raise PicoException('Another shell server with this server_number ' +
                            'already exists.', status_code=409)

    success = db.shell_servers.find_one_and_update(
        {'sid': sid}, {'$set': updates})
    if not success:
        return None
    else:
        return sid


def remove_server(sid):
    """
    Remove a shell server from the pool of servers.

    Args:
        sid: the sid of the server to be removed

    Returns:
        sid of the removed shell server, or
        None if the provided sid was not found
    """
    db = api.db.get_conn()
    res = db.shell_servers.find_one_and_delete({"sid": sid})
    if res is None:
        return None
    else:
        return sid


def get_servers(get_all=False):
    """
    Return the list of added shell servers, or the assigned shard.

    Depends on whether sharding is enabled.
    Defaults to server 1 if not assigned.
    """
    db = api.db.get_conn()

    settings = api.config.get_settings()
    match = {}
    if not get_all and settings["shell_servers"]["enable_sharding"]:
        team = api.team.get_team()
        match = {"server_number": team.get("server_number", 1)}

    servers = list(db.shell_servers.find(match, {"_id": 0}))

    if len(servers) == 0 and settings["shell_servers"]["enable_sharding"]:
        raise InternalException("Your assigned shell server is currently down."
                                + "Please contact an admin.")

    return servers


def get_problem_status_from_server(sid):
    """
    Connect to the server and check the status of the problems running there.

    Runs `sudo shell_manager status --json` and parses its output.

    Closes connection after running command.

    Args:
        sid: The sid of the server to check

    Returns:
        A tuple containing:
            - True if all problems are online and false otherwise
            - The output data of shell_manager status --json

    """
    shell = get_connection(sid)
    ensure_setup(shell)

    with shell:
        output = shell.run(
            ["sudo", "/picoCTF-env/bin/shell_manager", "status",
             "--json"]).output.decode("utf-8")
    data = json.loads(output)

    all_online = True

    for problem in data["problems"]:
        for instance in problem["instances"]:
            # if the service is not working
            if not instance["service"]:
                all_online = False

            # if the connection is not working and it is a remote challenge
            if not instance["connection"] and instance["port"] is not None:
                all_online = False

    return (all_online, data)


def load_problems_from_server(sid):
    """
    Connect to the server and loads the problems from its deployment state.

    Runs `sudo shell_manager publish` and captures its output.

    Closes connection after running command.

    Args:
        sid: The sid of the server to load problems from.

    Returns:
        The number of problems loaded

    """
    shell = get_connection(sid)

    with shell:
        result = shell.run(
            ["sudo", "/picoCTF-env/bin/shell_manager", "publish"])
    data = json.loads(result.output.decode("utf-8"))

    # Pass along the server
    data["sid"] = sid

    api.problem.load_published(data)

    return len(
        list(filter(lambda p: len(p["instances"]) > 0, data["problems"])))


def get_assigned_server_number(new_team=True, tid=None):
    """
    Assign a server number based on current team count and configured stepping.

    Returns:
         (int) server_number

    """
    settings = api.config.get_settings()["shell_servers"]
    db = api.db.get_conn()

    if new_team:
        team_count = db.teams.count()
    else:
        if not tid:
            raise InternalException("tid must be specified.")
        oid = db.teams.find_one({"tid": tid}, {"_id": 1})
        if not oid:
            raise InternalException("Invalid tid.")
        team_count = db.teams.count({"_id": {"$lt": oid["_id"]}})

    assigned_number = 1

    steps = settings["steps"]

    if steps:
        if team_count < steps[-1]:
            for i, step in enumerate(steps):
                if team_count < step:
                    assigned_number = i + 1
                    break
        else:
            assigned_number = 1 + len(steps) + (
                team_count - steps[-1]) // settings["default_stepping"]

    else:
        assigned_number = team_count // settings["default_stepping"] + 1

    if settings["limit_added_range"]:
        max_number = list(
            db.shell_servers.find({}, {
                "server_number": 1
            }).sort("server_number", -1).limit(1))[0]["server_number"]
        return min(max_number, assigned_number)
    else:
        return assigned_number


def reassign_teams(include_assigned=False):
    """Reassign teams to shell servers."""
    db = api.db.get_conn()

    if include_assigned:
        teams = api.team.get_all_teams(include_ineligible=True)
    else:
        teams = list(
            db.teams.find({"server_number": {
                "$exists": False
            }}, {
                "_id": 0,
                "tid": 1
            }))

    for team in teams:
        old_server_number = team.get("server_number")
        server_number = get_assigned_server_number(
            new_team=False, tid=team["tid"])
        if old_server_number != server_number:
            db.teams.update(
                {'tid': team["tid"]},
                {'$set': {
                    'server_number': server_number,
                    'instances': {}
                }})
            # Re-assign instances
            safe_fail(api.problem.get_visible_problems, team["tid"])

    return len(teams)
