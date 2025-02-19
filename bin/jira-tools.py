#!/usr/bin/env python3
"""
A minimal CLI for interacting with our day to day Jira tasks.

This script expects a ~/.jira_credentials file to exist with the following
format:
```yaml
user: "<your canonical email address>"
token: "<a valid Jira API token>"
```

The "--credentials=/path/to/credentials" flag can be used to specify an
alternate location for the credentials file.

See here for details of how to obtain a Jira API token:
https://developer.atlassian.com/cloud/jira/platform/basic-auth-for-rest-apis/

See the "example-pulse.yaml" file in the /resources directory for an example
of the file structure used for creating pulses.


Usage:
  # Write the list of the open Epics in a given backlog to stdout
  ./jira.py list-epics --backlog=UDENG

  # Create a new pulse in Jira from a YAML file containing tickets details
  ./jira.py new-pulse --path=my-pulse.yaml

  # Add tickets to an existing pulse in Jira
  ./jira.py add-to-pulse --path=my-pulse.yaml
"""
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional
import argparse
import os
import sys

# Useful docs & links:
#  https://developer.atlassian.com/cloud/jira/platform/basic-auth-for-rest-apis/
#  https://developer.atlassian.com/cloud/jira/software/rest/intro/#introduction
#  https://docs.atlassian.com/software/jira/docs/api/REST/9.17.0/#api/2/
#  https://yaml-multiline.info/


def log(msg: str):
    print(f">>> {msg}", file=sys.stderr)


try:
    import requests
    import yaml
except ModuleNotFoundError:
    log("WARNING: Missing dependencies")
    log("\t\tplease run 'sudo apt-get install python3-requests python3-yaml'")
    sys.exit(1)

JIRA_URL = 'https://warthogs.atlassian.net'


@dataclass
class Credentials:
    user: str
    token: str


@dataclass
class Issue:
    title: str
    parent: str
    description: str
    issue_type: str
    story_points: Optional[int] = None
    labels: list[str] = field(default_factory=list)
    components: list[str] = field(default_factory=list)
    fix_versions: list[str] = field(default_factory=list)

    def __post_init__(self):
        if self.issue_type not in ["Story", "Task", "Bug"]:
            raise ValueError(
                f"unknown issue type '{self.issue_type}': "
                "expected Story, Task or Bug"
            )


@dataclass
class Pulse:
    backlog: str
    board_id: int
    pulse_name: str
    pulse_goal: str
    start_date: datetime
    duration_days: int
    issues: list[Issue]
    existing_issues: list[str] = field(default_factory=list)
    shared_components: list[str] = field(default_factory=list)
    shared_labels: list[str] = field(default_factory=list)
    shared_fix_versions: list[str] = field(default_factory=list)

    def __post_init__(self):
        self.start_date = datetime.fromisoformat(self.start_date)
        self.issues = [Issue(**i) for i in self.issues]


class JiraClient:
    def __init__(self, creds: Credentials):
        self._credentials = creds

    def new_pulse(self, path: str):
        with open(os.path.expanduser(path)) as f:
            p = Pulse(**yaml.safe_load(f))

        log("Creating new pulse in Jira...")
        pulse_id = self._new_pulse(p)
        self._create_and_add_issues(p, pulse_id)

    def add_to_pulse(self, path: str):
        with open(os.path.expanduser(args.path)) as f:
            p = Pulse(**yaml.safe_load(f))

        log(f"Fetching ID of existing pulse: '{p.pulse_name}'...")
        pulse_id = self._get_pulse_id(p.board_id, p.pulse_name)
        log("Pulse ID found")
        self._create_and_add_issues(p, pulse_id)

    def _create_and_add_issues(self, p: Pulse, pulse_id: int):
        log(f"Creating {len(p.issues)} issues...")
        issues = []
        for issue in p.issues:
            key = self._new_issue(
                issue,
                p.backlog,
                p.shared_labels,
                p.shared_components,
                p.shared_fix_versions
            )
            issues.append(key)

        log("Adding issues to pulse")
        issues.extend(p.existing_issues)
        self._add_issues_to_pulse(issues, pulse_id)
        log("Done")

    def print_epics(self, project: str):
        jql = f"project={project} AND issuetype=Epic AND statusCategory!=Done"
        start = 0
        page_size = 100

        log(f"Pulling Epic details for {project}...")
        while True:
            payload = {
                "jql": jql,
                "fields": ["summary"],
                "maxResults": page_size,
                "startAt": start
            }
            resp = requests.post(
                f"{JIRA_URL}/rest/api/2/search",
                json=payload,
                auth=self._auth()
            )
            resp.raise_for_status()
            data = resp.json()

            if len(data["issues"]) == 0:
                return

            for issue in data["issues"]:
                print(f"{issue["key"]}\t{issue["fields"]["summary"]}")
            start += page_size

    def _auth(self):
        return (self._credentials.user, self._credentials.token)

    def _new_pulse(self, p: Pulse) -> int:
        end_date = p.start_date + timedelta(days=p.duration_days)
        payload = {
            "name": p.pulse_name,
            "goal": p.pulse_goal,
            "originBoardId": p.board_id,
            "startDate": p.start_date.isoformat(),
            "endDate": end_date.isoformat(),
        }
        resp = requests.post(
            f"{JIRA_URL}/rest/agile/1.0/sprint",
            json=payload,
            auth=self._auth()
        )
        resp.raise_for_status()
        id = resp.json()["id"]

        return id

    def _new_issue(
        self,
        i: Issue,
        backlog: str,
        labels: list[str],
        components: list[str],
        fix_versions: list[str]
    ) -> str:
        i.labels.extend(labels)
        i.components.extend(components)
        i.fix_versions.extend(fix_versions)

        payload = {
            "fields": {
                "assignee": None,
                "project": {"key": backlog},
                "summary": i.title,
                "description": i.description,
                "labels": i.labels,
                "components": [{"name": comp} for comp in i.components],
                "fixVersions": [{"name": v} for v in i.fix_versions],
                "issuetype": {"name": i.issue_type},
            }
        }
        if i.story_points is not None:
            payload["fields"]["customfield_10024"] = i.story_points

        resp = requests.post(
            f"{JIRA_URL}/rest/api/2/issue",
            json=payload,
            auth=self._auth()
        )
        print(resp.json())
        resp.raise_for_status()
        key = resp.json()["key"]

        # Assigning an issue to a parent epic doesn't seem to work on issue
        # creation so we need to make a second API call to move the issue
        # into the correct epic
        resp = requests.post(
            f"{JIRA_URL}/rest/agile/1.0/epic/{i.parent}/issue",
            json={"issues": [key]},
            auth=self._auth()
        )
        resp.raise_for_status()
        log(f"https://warthogs.atlassian.net/browse/{key} created")

        return key

    def _add_issues_to_pulse(self, issues: list[str], pulse_id: int):
        payload = {"issues": issues}
        resp = requests.post(
            f"{JIRA_URL}/rest/agile/1.0/sprint/{pulse_id}/issue",
            json=payload,
            auth=self._auth()
        )
        resp.raise_for_status()

    def _get_pulse_id(self, board_id: int, name: str) -> int:
        start = 0
        while True:
            resp = requests.get(
                f"{JIRA_URL}/rest/agile/1.0/board/{board_id}/sprint",
                params={"startAt": start},
                auth=self._auth()
            )
            resp.raise_for_status()
            data = resp.json()
            n = len(data["values"])
            if n == 0:
                raise ValueError(f"Unknown pulse name: '{name}'")

            for item in data["values"]:
                if item["name"] == name:
                    return item["id"]

            start += n


def arg_parser():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        '--credentials',
        type=str,
        default=os.path.expanduser('~/.jira_credentials'),
        help='Path to yaml file with JIRA credentials.'
    )

    subparsers = parser.add_subparsers(dest='subparser_name')

    epics_parser = subparsers.add_parser(
        "list-epics",
        description="list epics for a backlog"
    )
    epics_parser.add_argument(
        '--backlog',
        type=str,
        required=True,
        help='The backlog to list open epics in'
    )

    pulse_parser = subparsers.add_parser(
        "new-pulse",
        description="create a new pulse in Jira"
    )
    pulse_parser.add_argument(
        '--path',
        type=str,
        required=True,
        help='Path to yaml file with the pulse contents'
    )

    existing_pulse_parser = subparsers.add_parser(
        "add-to-pulse",
        description="update an existing pulse in Jira"
    )
    existing_pulse_parser.add_argument(
        '--path',
        type=str,
        required=True,
        help='Path to yaml file with the pulse contents'
    )

    return parser


if __name__ == '__main__':
    parser = arg_parser()
    args = parser.parse_args()

    with open(os.path.expanduser(args.credentials)) as f:
        raw = yaml.safe_load(f)
        client = JiraClient(Credentials(user=raw["user"], token=raw["token"]))

    if args.subparser_name == "list-epics":
        client.print_epics(args.backlog)
    elif args.subparser_name == "new-pulse":
        client.new_pulse(args.path)
    elif args.subparser_name == "add-to-pulse":
        client.add_to_pulse(args.path)
    else:
        print(parser.format_help())
        sys.exit(1)
