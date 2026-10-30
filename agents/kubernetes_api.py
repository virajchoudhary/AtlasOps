from __future__ import annotations

import inspect
from typing import Any


def request_object(
    client: Any,
    path: str,
    method: str,
    *,
    headers: dict[str, str],
    body: Any = None,
    object_statuses: tuple[int, ...] = (200, 201, 202),
) -> Any:
    parameters = inspect.signature(client.call_api).parameters
    if "url" in parameters:
        serialized = client.param_serialize(
            method, path, header_params=headers, body=body,
            auth_settings=["BearerToken"],
        )
        response = client.call_api(*serialized, _request_timeout=30)
        response.read()
        return client.response_deserialize(
            response_data=response,
            response_types_map={str(status): "object" for status in object_statuses},
        ).data

    response_options = (
        {"response_types_map": {status: "object" for status in object_statuses}}
        if "response_types_map" in parameters
        else {"response_type": "object"}
    )
    return client.call_api(
        path, method, header_params=headers, body=body,
        auth_settings=["BearerToken"], **response_options,
        _return_http_data_only=True, _request_timeout=30,
    )
