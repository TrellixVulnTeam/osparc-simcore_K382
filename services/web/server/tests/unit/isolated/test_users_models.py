from copy import deepcopy
from datetime import datetime
from pprint import pformat
from typing import Any

import pytest
from faker import Faker
from models_library.generics import Envelope
from models_library.utils.fastapi_encoders import jsonable_encoder
from pydantic import BaseModel
from simcore_postgres_database.models.users import UserRole
from simcore_service_webserver.users_models import ProfileGet, Token


@pytest.mark.parametrize(
    "model_cls",
    (
        ProfileGet,
        Token,
    ),
)
def test_user_models_examples(
    model_cls: type[BaseModel], model_cls_examples: dict[str, Any]
):
    for name, example in model_cls_examples.items():
        print(name, ":", pformat(example))
        model_instance = model_cls(**example)
        assert model_instance, f"Failed with {name}"

        #
        # TokenEnveloped
        # TokensArrayEnveloped
        # TokenIdEnveloped
        # ProfileEnveloped
        #

        model_enveloped = Envelope[model_cls].parse_data(
            model_instance.dict(by_alias=True)
        )
        model_array_enveloped = Envelope[list[model_cls]].parse_data(
            [
                model_instance.dict(by_alias=True),
                model_instance.dict(by_alias=True),
            ]
        )

        assert model_enveloped.error is None
        assert model_array_enveloped.error is None


def test_profile_get_expiration_date(faker: Faker):

    fake_expiration = datetime.utcnow()

    profile = ProfileGet(
        id=1, login=faker.email(), role=UserRole.ADMIN, expiration_date=fake_expiration
    )

    assert fake_expiration.date() == profile.expiration_date

    # TODO: encoding in body!? UTC !! ??
    body = jsonable_encoder(profile.dict(exclude_unset=True, by_alias=True))
    assert body["expirationDate"] == fake_expiration.date().isoformat()


@pytest.mark.parametrize("user_role", [u.name for u in UserRole])
def test_profile_get_role(user_role: str):
    for example in ProfileGet.Config.schema_extra["examples"]:
        data = deepcopy(example)
        data["role"] = user_role
        m1 = ProfileGet(**data)

        data["role"] = UserRole(user_role)
        m2 = ProfileGet(**data)
        assert m1 == m2


def test_parsing_output_of_get_user_profile():

    result_from_db_query_and_composition = {
        "id": 1,
        "login": "PtN5Ab0uv@guest-at-osparc.io",
        "first_name": "PtN5Ab0uv",
        "last_name": "",
        "role": "Guest",
        "gravatar_id": "9d5e02c75fcd4bce1c8861f219f7f8a5",
        "groups": {
            "me": {
                "gid": 2,
                "label": "PtN5Ab0uv",
                "description": "primary group",
                "thumbnail": None,
                "inclusionRules": {},
                "accessRights": {"read": True, "write": False, "delete": False},
            },
            "organizations": [],
            "all": {
                "gid": 1,
                "label": "Everyone",
                "description": "all users",
                "thumbnail": None,
                "inclusionRules": {},
                "accessRights": {"read": True, "write": False, "delete": False},
            },
        },
        "password": "secret",  # should be stripped out
    }

    profile = ProfileGet.parse_obj(result_from_db_query_and_composition)
    assert "password" not in profile.dict(exclude_unset=True)
