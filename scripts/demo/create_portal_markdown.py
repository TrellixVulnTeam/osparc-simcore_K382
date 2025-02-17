""" This script produces a markdown document with links to template studies

    Aims to emulate links

"""

# TODO: extend cli to generate invitations use jinja templates (see folder)

import argparse
import json
import logging
import sys
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from string import ascii_uppercase
from typing import Optional

from simcore_service_webserver.login._registration import get_invitation_url
from simcore_service_webserver.login.utils import get_random_string
from yarl import URL

current_path = Path(sys.argv[0] if __name__ == "__main__" else __file__).resolve()
current_dir = current_path.parent

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

CONFIRMATIONS_FILENAME = "ignore-confirmations.csv"

ISSUE = r"https://github.com/ITISFoundation/osparc-simcore/issues/"

HOST_URLS_MAPS = [
    ("localhost", r"http://127.0.0.1:9081"),
    ("master", r"https://master.osparc.io"),
    ("staging", r"https://staging.osparc.io"),
    ("production", r"https://osparc.io"),
]

N = len(ascii_uppercase)
NUM_CODES = 15
CODE_LEN = 30
default_mock_codes = [ascii_uppercase[i % N] * CODE_LEN for i in range(NUM_CODES)]

params = {}
params["194bb264-a717-11e9-9dff-02420aff2767"] = {
    "stimulation_mode": "1",
    "stimulation_level": "0.5",
}


@contextmanager
def _open(filepath):
    filepath = Path(filepath)
    log.info("Writing %s ... ", filepath)
    with open(filepath, "wt") as fh:
        yield fh
    log.info("%s ready", filepath.name)


def write_list(hostname, url, data, fh):
    origin = URL(url)

    print(f"## studies available @{hostname}", file=fh)
    print("", file=fh)
    for prj in data:
        prj["msg"] = ""
        study_url = origin.with_path("study/{uuid}".format(**prj))
        if prj["uuid"] in params:
            prj_params = params[prj["uuid"]]
            study_url = study_url.with_query(**prj_params)
            prj["msg"] = "with " + "and ".join(
                [f"{k}={v} " for k, v in prj_params.items()]
            )
        print(
            "- [{name}]({study_url}) {msg}".format(study_url=str(study_url), **prj),
            file=fh,
        )
    print("", file=fh)


def main(mock_codes, *, trial_account_days: Optional[int] = None, uid: int = 1):
    data = {}

    with open(current_dir / "template-projects/templates_in_master.json") as fp:
        data["master"] = json.load(fp)

    file_path = str(current_path.with_suffix(".md")).replace("create_", "ignore-")
    with _open(file_path) as fh:
        print(
            "<!-- Generated by {} on {} -->".format(
                current_path.name, datetime.utcnow()
            ),
            file=fh,
        )
        print("# THE PORTAL Emulator\n", file=fh)
        print(
            "This pages is for testing purposes for issue [#{1}]({0}{1})\n".format(
                ISSUE, 715
            ),
            file=fh,
        )
        for hostname, url in HOST_URLS_MAPS:
            write_list(hostname, url, data.get(hostname, []), fh)

        print("---", file=fh)

        print("# INVITATIONS Samples:", file=fh)
        for hostname, url in HOST_URLS_MAPS:
            print(f"## urls for @{hostname}", file=fh)
            for code in mock_codes:
                print(
                    "- {}".format(
                        get_invitation_url(
                            {"code": code, "action": "INVITATION"}, URL(url)
                        ),
                        code=code,
                    ),
                    file=fh,
                )

        print("", file=fh)

    today: datetime = datetime.today()
    file_path = current_path.parent / CONFIRMATIONS_FILENAME
    with _open(file_path) as fh:
        print("code,user_id,action,data,created_at", file=fh)
        for n, code in enumerate(mock_codes, start=1):
            print(f'{code},{uid},INVITATION,"{{', file=fh)
            print(
                f'""tag"": ""invitation-{today.year:04d}{today.month:02d}{today.day:02d}-{n}"" ,',
                file=fh,
            )
            print('""issuer"" : ""support@osparc.io"" ,', file=fh)
            print(f'""trial_account_days"" : ""{trial_account_days}""', file=fh)
            print('}",%s' % datetime.now().isoformat(sep=" "), file=fh)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generates some material for demos")
    parser.add_argument(
        "--renew-invitation-codes",
        "-c",
        action="store_true",
        help="Regenerates codes for invitations",
    )
    parser.add_argument("--user-id", "-u", default=1)
    parser.add_argument("--trial-days", "-t", default=7)

    args = parser.parse_args()

    codes = default_mock_codes
    if args.renew_invitation_codes:
        codes = [get_random_string(len(c)) for c in default_mock_codes]

    main(codes, uid=args.user_id, trial_account_days=args.trial_days)
