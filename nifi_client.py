"""Minimal NiFi 1.x REST client for local automation."""

from __future__ import annotations

import os
import re
import time
import urllib3
from typing import Any

import requests
from dotenv import load_dotenv

# header.payload.signature — used to tell a real token from an HTML error page.
_JWT = re.compile(r"^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]*$")

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
# Do not override process env (UI can inject NIFI_* per run).
load_dotenv(override=False)


class NiFiClient:
    def __init__(
        self,
        base_url: str | None = None,
        username: str | None = None,
        password: str | None = None,
    ) -> None:
        self.raw_base_url = (base_url or os.getenv("NIFI_URL", "https://127.0.0.1:8443")).strip()
        self.base_url = self._normalize_base_url(self.raw_base_url)
        self.username = username or os.getenv("NIFI_USERNAME", "")
        self.password = password or os.getenv("NIFI_PASSWORD", "")
        # Credentials are not validated here: an instance with authentication disabled
        # (the default for many older NiFi installs on plain HTTP) has none to give.
        # login() decides, so the error can distinguish "no credentials" from
        # "credentials rejected".
        self.session = requests.Session()
        self.session.verify = False
        self._token: str | None = None

    @staticmethod
    def _normalize_base_url(raw: str) -> str:
        """Reduce a pasted NiFi address to the server root the REST API lives under.

        People paste the address bar — `https://host:8443/nifi/` (1.x UI) or
        `https://host:8443/nf/` (2.x UI). Left alone, requests go to
        `.../nifi/nifi-api/...`, which the single-page app answers with HTTP 200 and
        an HTML page rather than an error.
        """
        url = (raw or "").strip()
        url = url.split("#", 1)[0].split("?", 1)[0].rstrip("/")
        changed = True
        while changed:
            changed = False
            for suffix in ("/nifi-api", "/nifi", "/nf"):
                if url.endswith(suffix):
                    url = url[: -len(suffix)].rstrip("/")
                    changed = True
        return url

    def _candidate_base_urls(self) -> list[str]:
        """Normalized root first, then the address as typed.

        The fallback matters when NiFi genuinely runs under a context path (behind
        a proxy at `/nifi`, say), where stripping the suffix would be wrong.
        """
        raw = self.raw_base_url.split("#", 1)[0].split("?", 1)[0].rstrip("/")
        return [self.base_url] + ([raw] if raw and raw != self.base_url else [])

    def _request_token(self, base_url: str) -> str:
        resp = self.session.post(
            f"{base_url}/nifi-api/access/token",
            data={"username": self.username, "password": self.password},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=30,
        )
        self._raise_for_status(resp)
        body = (resp.text or "").strip()
        content_type = (resp.headers.get("Content-Type") or "").lower()
        if body.startswith("<") or "html" in content_type:
            # The NiFi UI serves its index page for unknown paths, so a 200 here
            # means we asked the web app, not the API.
            raise ValueError(
                "got an HTML page instead of a token — that address serves the NiFi "
                "UI, not the REST API"
            )
        if not _JWT.match(body):
            raise ValueError(f"response was not a token (began with {body[:40]!r})")
        return body

    def _has_credentials(self) -> bool:
        return bool(self.username) and bool(self.password) and self.username != "CHANGE_ME"

    def _unauthenticated_base_url(self) -> str | None:
        """Return the base URL that serves the API without a token, if any.

        NiFi can run with authentication disabled, in which case there is no token
        to obtain and every call simply works.
        """
        for candidate in self._candidate_base_urls():
            try:
                resp = self.session.get(f"{candidate}/nifi-api/flow/about", timeout=15)
            except requests.RequestException:
                continue
            if resp.status_code >= 300:
                continue
            try:
                # The UI answers unknown paths with HTML and HTTP 200, so require the
                # response to actually be the version document.
                if ((resp.json() or {}).get("about") or {}).get("version"):
                    return candidate
            except ValueError:
                continue
        return None

    def login(self) -> str:
        """Obtain a bearer token, or confirm the instance needs no authentication.

        Returns the token, or an empty string when authentication is disabled.
        """
        if not self._has_credentials():
            open_base = self._unauthenticated_base_url()
            if open_base is None:
                raise ValueError(
                    "Missing NiFi credentials. Provide NIFI_URL / NIFI_USERNAME / "
                    "NIFI_PASSWORD via the Flow Studio UI (preferred) or environment "
                    "variables. (This instance requires authentication.)"
                )
            self.base_url = open_base
            self._token = ""
            return ""

        failures: list[str] = []
        for candidate in self._candidate_base_urls():
            try:
                token = self._request_token(candidate)
            except (requests.RequestException, ValueError) as exc:
                failures.append(f"{candidate} -> {exc}")
                continue
            self.base_url = candidate
            self._token = token
            self.session.headers["Authorization"] = f"Bearer {token}"
            # NiFi CSRF: mutating calls need Request-Token (from access cookie/header).
            # Not every release exposes /access to a bearer token, so this is best effort.
            try:
                access = self.session.get(f"{candidate}/nifi-api/access", timeout=30)
                if access.status_code < 400:
                    self._refresh_request_token(access)
            except requests.RequestException:
                pass
            return token
        # Credentials were supplied but no token could be had. If the instance serves
        # the API without one, it has authentication disabled and the credentials are
        # simply irrelevant — proceed rather than failing.
        open_base = self._unauthenticated_base_url()
        if open_base is not None:
            self.base_url = open_base
            self._token = ""
            return ""
        raise RuntimeError(
            "Could not authenticate to NiFi. Tried: " + "; ".join(failures)
        )

    def _refresh_request_token(self, resp: requests.Response | None = None) -> None:
        token = None
        if resp is not None:
            token = resp.headers.get("Request-Token")
        if not token:
            token = self.session.cookies.get("__Secure-Request-Token") or self.session.cookies.get(
                "Request-Token"
            )
        if token:
            self.session.headers["Request-Token"] = token

    @staticmethod
    def _raise_for_status(resp: requests.Response) -> None:
        """Raise with NiFi's explanation attached.

        NiFi returns the useful part of a 4xx (e.g. which property is invalid) in
        the response body, which `raise_for_status` alone discards.
        """
        if resp.status_code < 400:
            return
        detail = (resp.text or "").strip()
        raise requests.HTTPError(
            f"{resp.status_code} {resp.reason} for {resp.request.method} {resp.url}"
            + (f"\nNiFi said: {detail[:1000]}" if detail else ""),
            response=resp,
        )

    def get(self, path: str) -> Any:
        resp = self.session.get(f"{self.base_url}{path}", timeout=30)
        self._raise_for_status(resp)
        self._refresh_request_token(resp)
        return resp.json() if resp.content else None

    def post(self, path: str, payload: dict[str, Any]) -> Any:
        self._refresh_request_token()
        resp = self.session.post(
            f"{self.base_url}{path}",
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=30,
        )
        self._raise_for_status(resp)
        self._refresh_request_token(resp)
        return resp.json() if resp.content else None

    def put(self, path: str, payload: dict[str, Any]) -> Any:
        self._refresh_request_token()
        resp = self.session.put(
            f"{self.base_url}{path}",
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=30,
        )
        self._raise_for_status(resp)
        self._refresh_request_token(resp)
        return resp.json() if resp.content else None

    def about(self) -> dict[str, Any]:
        data = self.get("/nifi-api/flow/about") or {}
        return data.get("about") or {}

    def nifi_version(self) -> str:
        return str(self.about().get("version") or "").strip()

    def list_processor_types(self) -> list[dict[str, Any]]:
        data = self.get("/nifi-api/flow/processor-types") or {}
        return list(data.get("processorTypes") or [])

    def list_controller_service_types(self) -> list[dict[str, Any]]:
        data = self.get("/nifi-api/flow/controller-service-types") or {}
        return list(data.get("controllerServiceTypes") or [])

    def root_process_group_id(self) -> str:
        data = self.get("/nifi-api/flow/process-groups/root")
        return data["processGroupFlow"]["id"]

    def create_process_group(self, parent_id: str, name: str, x: float = 0, y: float = 0) -> dict[str, Any]:
        return self.post(
            f"/nifi-api/process-groups/{parent_id}/process-groups",
            {
                "revision": {"version": 0},
                "component": {
                    "name": name,
                    "position": {"x": x, "y": y},
                },
            },
        )

    def create_processor(
        self,
        parent_id: str,
        name: str,
        type_name: str,
        x: float,
        y: float,
        properties: dict[str, str] | None = None,
        scheduling_period: str | None = None,
        bundle: dict[str, Any] | None = None,
        config_extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        config: dict[str, Any] = {
            "properties": properties or {},
            "autoTerminatedRelationships": [],
        }
        if scheduling_period:
            config["schedulingPeriod"] = scheduling_period
        if config_extra:
            config.update(config_extra)

        component: dict[str, Any] = {
            "name": name,
            "type": type_name,
            "position": {"x": x, "y": y},
            "config": config,
        }
        if bundle and bundle.get("artifact"):
            component["bundle"] = {
                "group": bundle.get("group"),
                "artifact": bundle.get("artifact"),
                "version": bundle.get("version"),
            }

        return self.post(
            f"/nifi-api/process-groups/{parent_id}/processors",
            {
                "revision": {"version": 0},
                "component": component,
            },
        )

    def update_processor(
        self,
        processor_id: str,
        revision_version: int,
        *,
        properties: dict[str, str] | None = None,
        auto_terminated: list[str] | None = None,
        scheduling_period: str | None = None,
        name: str | None = None,
        config_extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        current = self.get(f"/nifi-api/processors/{processor_id}")
        component = current["component"]
        config = component.get("config", {})
        if properties is not None:
            config["properties"] = {**config.get("properties", {}), **properties}
        if auto_terminated is not None:
            config["autoTerminatedRelationships"] = auto_terminated
        if scheduling_period is not None:
            config["schedulingPeriod"] = scheduling_period
        if config_extra:
            config.update(config_extra)
        if name is not None:
            component["name"] = name
        component["config"] = config
        return self.put(
            f"/nifi-api/processors/{processor_id}",
            {
                "revision": {"version": revision_version, "clientId": current["revision"].get("clientId")},
                "component": {
                    "id": processor_id,
                    "name": component["name"],
                    "config": config,
                },
            },
        )

    def create_controller_service(
        self,
        parent_id: str,
        name: str,
        type_name: str,
        properties: dict[str, str] | None = None,
        bundle: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        component: dict[str, Any] = {
            "name": name,
            "type": type_name,
            "properties": properties or {},
        }
        if bundle and bundle.get("artifact"):
            component["bundle"] = {
                "group": bundle.get("group"),
                "artifact": bundle.get("artifact"),
                "version": bundle.get("version"),
            }
        return self.post(
            f"/nifi-api/process-groups/{parent_id}/controller-services",
            {
                "revision": {"version": 0},
                "component": component,
            },
        )

    def enable_controller_service(self, service_id: str) -> dict[str, Any]:
        current = self.get(f"/nifi-api/controller-services/{service_id}")
        return self.put(
            f"/nifi-api/controller-services/{service_id}/run-status",
            {
                "revision": {
                    "version": current["revision"]["version"],
                    "clientId": current["revision"].get("clientId"),
                },
                "state": "ENABLED",
                "disconnectedNodeAcknowledged": False,
            },
        )

    def wait_for_controller_service(
        self, service_id: str, state: str = "ENABLED", timeout: float = 30.0
    ) -> str:
        """Block until a service reaches `state`.

        Enabling is asynchronous. Starting processors while a service is still
        ENABLING leaves them invalid ("Controller Service ... is disabled") and
        they silently refuse to start.
        """
        deadline = time.time() + timeout
        current = ""
        while time.time() < deadline:
            component = (self.get(f"/nifi-api/controller-services/{service_id}") or {}).get(
                "component"
            ) or {}
            current = str(component.get("state") or "")
            if current == state:
                return current
            if current in ("DISABLED", "INVALID") and state == "ENABLED":
                errors = component.get("validationErrors") or []
                if errors:
                    raise RuntimeError(
                        f"Controller service {component.get('name') or service_id} "
                        f"cannot enable: {'; '.join(errors)}"
                    )
            time.sleep(0.5)
        return current

    def create_connection(
        self,
        parent_id: str,
        source_id: str,
        destination_id: str,
        relationships: list[str],
        name: str = "",
        source_type: str = "PROCESSOR",
        destination_type: str = "PROCESSOR",
        source_group_id: str | None = None,
        destination_group_id: str | None = None,
        settings: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Create a connection between any two connectable components.

        `parent_id` is the process group the connection is drawn in (must be the
        direct parent of at least one endpoint, and the common ancestor of both).
        `source_type`/`destination_type` are one of PROCESSOR, INPUT_PORT,
        OUTPUT_PORT, FUNNEL, REMOTE_INPUT_PORT, REMOTE_OUTPUT_PORT — this is how
        NiFi links across process-group boundaries (via ports) and through funnels.

        `settings` overrides queue behaviour: flowFileExpiration, back pressure
        thresholds, prioritizers, and cluster load balancing.
        """
        component: dict[str, Any] = {
            "name": name,
            "source": {
                "id": source_id,
                "groupId": source_group_id or parent_id,
                "type": source_type,
            },
            "destination": {
                "id": destination_id,
                "groupId": destination_group_id or parent_id,
                "type": destination_type,
            },
            "selectedRelationships": relationships,
            "flowFileExpiration": "0 sec",
            "backPressureDataSizeThreshold": "1 GB",
            "backPressureObjectThreshold": 10000,
        }
        if settings:
            component.update(settings)
        return self.post(
            f"/nifi-api/process-groups/{parent_id}/connections",
            {"revision": {"version": 0}, "component": component},
        )

    def create_input_port(
        self,
        parent_id: str,
        name: str,
        x: float = 0,
        y: float = 0,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        component: dict[str, Any] = {"name": name, "position": {"x": x, "y": y}}
        if extra:
            component.update(extra)
        return self.post(
            f"/nifi-api/process-groups/{parent_id}/input-ports",
            {"revision": {"version": 0}, "component": component},
        )

    def create_output_port(
        self,
        parent_id: str,
        name: str,
        x: float = 0,
        y: float = 0,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        component: dict[str, Any] = {"name": name, "position": {"x": x, "y": y}}
        if extra:
            component.update(extra)
        return self.post(
            f"/nifi-api/process-groups/{parent_id}/output-ports",
            {"revision": {"version": 0}, "component": component},
        )

    def update_connection(self, connection_id: str, settings: dict[str, Any]) -> dict[str, Any]:
        """Apply queue settings (back pressure, expiration, prioritizers, load balancing)."""
        current = self.get(f"/nifi-api/connections/{connection_id}")
        component: dict[str, Any] = {
            "id": connection_id,
            # NiFi revalidates the endpoints on update, so echo them back.
            "source": current["component"]["source"],
            "destination": current["component"]["destination"],
        }
        component.update(settings)
        return self.put(
            f"/nifi-api/connections/{connection_id}",
            {
                "revision": {
                    "version": current["revision"]["version"],
                    "clientId": current["revision"].get("clientId"),
                },
                "component": component,
            },
        )

    def update_port(self, port_id: str, kind: str, **fields: Any) -> dict[str, Any]:
        """Set port options (concurrent tasks, remote access, comments).

        A port ignores these on create, so they have to be PUT afterwards — and
        while the port is still stopped.
        """
        path = "input-ports" if kind == "INPUT_PORT" else "output-ports"
        current = self.get(f"/nifi-api/{path}/{port_id}")
        component: dict[str, Any] = {"id": port_id}
        component.update({k: v for k, v in fields.items() if v is not None})
        return self.put(
            f"/nifi-api/{path}/{port_id}",
            {
                "revision": {
                    "version": current["revision"]["version"],
                    "clientId": current["revision"].get("clientId"),
                },
                "component": component,
            },
        )

    def create_funnel(self, parent_id: str, x: float = 0, y: float = 0) -> dict[str, Any]:
        return self.post(
            f"/nifi-api/process-groups/{parent_id}/funnels",
            {
                "revision": {"version": 0},
                "component": {"position": {"x": x, "y": y}},
            },
        )

    def create_label(
        self,
        parent_id: str,
        text: str,
        x: float = 0,
        y: float = 0,
        width: float = 260,
        height: float = 120,
    ) -> dict[str, Any]:
        return self.post(
            f"/nifi-api/process-groups/{parent_id}/labels",
            {
                "revision": {"version": 0},
                "component": {
                    "label": text,
                    "position": {"x": x, "y": y},
                    "width": width,
                    "height": height,
                },
            },
        )

    def start_process_group(self, group_id: str) -> dict[str, Any]:
        return self.put(
            f"/nifi-api/flow/process-groups/{group_id}",
            {
                "id": group_id,
                "state": "RUNNING",
            },
        )

    def stop_process_group(self, group_id: str) -> dict[str, Any]:
        return self.put(
            f"/nifi-api/flow/process-groups/{group_id}",
            {"id": group_id, "state": "STOPPED", "disconnectedNodeAcknowledged": False},
        )

    def set_group_controller_services(self, group_id: str, state: str) -> dict[str, Any]:
        """Bulk ENABLED/DISABLED for every controller service in a group."""
        return self.put(
            f"/nifi-api/flow/process-groups/{group_id}/controller-services",
            {"id": group_id, "state": state, "disconnectedNodeAcknowledged": False},
        )

    def update_process_group(self, group_id: str, **fields: Any) -> dict[str, Any]:
        """Set process-group settings: comments, FlowFile concurrency/outbound policy,
        group-level queue defaults, or an attached parameter context."""
        current = self.get(f"/nifi-api/process-groups/{group_id}")
        component: dict[str, Any] = {"id": group_id}
        component.update({k: v for k, v in fields.items() if v is not None})
        return self.put(
            f"/nifi-api/process-groups/{group_id}",
            {
                "revision": {
                    "version": current["revision"]["version"],
                    "clientId": current["revision"].get("clientId"),
                },
                "component": component,
            },
        )

    def connection_ids(self, group_id: str) -> list[str]:
        """Every connection id in a group, including nested sub-groups."""
        data = self.get(f"/nifi-api/flow/process-groups/{group_id}") or {}
        flow = (data.get("processGroupFlow") or {}).get("flow") or {}
        ids = [c["id"] for c in flow.get("connections") or []]
        for child in flow.get("processGroups") or []:
            ids.extend(self.connection_ids(child["id"]))
        return ids

    def empty_queues(self, group_id: str) -> None:
        """Drop queued FlowFiles everywhere in a group. NiFi refuses to delete a
        group while any queue still holds data."""
        for conn_id in self.connection_ids(group_id):
            try:
                self.post(f"/nifi-api/flowfile-queues/{conn_id}/drop-requests", {})
            except requests.HTTPError:
                pass  # Nothing queued, or already dropping.

    def delete_process_group(self, group_id: str, wait_seconds: int = 40) -> int:
        """Stop, disable services, drain queues, then delete.

        Retries because stopping is asynchronous and NiFi rejects the delete
        while components are still running or a queue is non-empty.
        """
        for action in (
            lambda: self.stop_process_group(group_id),
            lambda: self.set_group_controller_services(group_id, "DISABLED"),
        ):
            try:
                action()
            except requests.HTTPError:
                pass  # Already stopped/no services; the delete below is the real check.

        deadline = time.time() + wait_seconds
        detail = ""
        while True:
            self.empty_queues(group_id)
            time.sleep(2)
            current = self.get(f"/nifi-api/process-groups/{group_id}")
            version = current["revision"]["version"]
            resp = self.session.delete(
                f"{self.base_url}/nifi-api/process-groups/{group_id}?version={version}",
                timeout=30,
            )
            self._refresh_request_token(resp)
            if resp.status_code < 300:
                return resp.status_code
            detail = (resp.text or "").strip()
            if time.time() >= deadline:
                break
        raise requests.HTTPError(
            f"Could not delete process group {group_id}: {resp.status_code} {detail[:300]}",
            response=resp,
        )

    def find_child_group_id(self, parent_id: str, name: str) -> str | None:
        """Newest child group of `parent_id` with this exact name."""
        data = self.get(f"/nifi-api/flow/process-groups/{parent_id}") or {}
        groups = ((data.get("processGroupFlow") or {}).get("flow") or {}).get("processGroups") or []
        matches = [g for g in groups if ((g.get("component") or {}).get("name")) == name]
        return matches[-1]["id"] if matches else None

    def create_parameter_context(
        self,
        name: str,
        parameters: list[dict[str, Any]],
        description: str = "",
    ) -> dict[str, Any]:
        """Create a parameter context. Properties then reference values as #{name}."""
        return self.post(
            "/nifi-api/parameter-contexts",
            {
                "revision": {"version": 0},
                "component": {
                    "name": name,
                    "description": description,
                    "parameters": [
                        {
                            "parameter": {
                                "name": p["name"],
                                "value": p.get("value"),
                                "sensitive": bool(p.get("sensitive", False)),
                                "description": p.get("description", ""),
                            }
                        }
                        for p in parameters
                    ],
                },
            },
        )

    def list_parameter_contexts(self) -> list[dict[str, Any]]:
        data = self.get("/nifi-api/flow/parameter-contexts") or {}
        return list(data.get("parameterContexts") or [])

    def create_remote_process_group(
        self,
        parent_id: str,
        target_uris: str,
        x: float = 0,
        y: float = 0,
        transport_protocol: str = "HTTP",
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Create a site-to-site remote process group pointing at `target_uris`."""
        component: dict[str, Any] = {
            "targetUris": target_uris,
            "transportProtocol": transport_protocol,
            "position": {"x": x, "y": y},
        }
        if extra:
            component.update(extra)
        return self.post(
            f"/nifi-api/process-groups/{parent_id}/remote-process-groups",
            {"revision": {"version": 0}, "component": component},
        )

    def create_controller_level_service(
        self,
        name: str,
        type_name: str,
        properties: dict[str, str] | None = None,
        bundle: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Create a controller-scoped (global) service, not tied to a process group."""
        component: dict[str, Any] = {
            "name": name,
            "type": type_name,
            "properties": properties or {},
        }
        if bundle and bundle.get("artifact"):
            component["bundle"] = {
                "group": bundle.get("group"),
                "artifact": bundle.get("artifact"),
                "version": bundle.get("version"),
            }
        return self.post(
            "/nifi-api/controller/controller-services",
            {"revision": {"version": 0}, "component": component},
        )

    def create_reporting_task(
        self,
        name: str,
        type_name: str,
        properties: dict[str, str] | None = None,
        scheduling_period: str | None = None,
        bundle: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        component: dict[str, Any] = {
            "name": name,
            "type": type_name,
            "properties": properties or {},
        }
        if scheduling_period:
            component["schedulingPeriod"] = scheduling_period
        if bundle and bundle.get("artifact"):
            component["bundle"] = {
                "group": bundle.get("group"),
                "artifact": bundle.get("artifact"),
                "version": bundle.get("version"),
            }
        return self.post(
            "/nifi-api/controller/reporting-tasks",
            {"revision": {"version": 0}, "component": component},
        )

    def set_reporting_task_state(self, task_id: str, state: str) -> dict[str, Any]:
        current = self.get(f"/nifi-api/reporting-tasks/{task_id}")
        return self.put(
            f"/nifi-api/reporting-tasks/{task_id}/run-status",
            {
                "revision": {
                    "version": current["revision"]["version"],
                    "clientId": current["revision"].get("clientId"),
                },
                "state": state,
                "disconnectedNodeAcknowledged": False,
            },
        )

    def list_reporting_task_types(self) -> list[dict[str, Any]]:
        data = self.get("/nifi-api/flow/reporting-task-types") or {}
        return list(data.get("reportingTaskTypes") or [])
