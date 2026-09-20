"""Studio Dev Direct Mode compatibility harness for FAULTLINE."""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

import gltest.direct.loader as direct_loader
from gltest.direct.vm import VMContext, _sentinel


STUDIO_DEV_RUNNER_HASH = "5jycge4q8k23462jtb0b9fyey1s9qz928sz2nbrd9mg4sxqg2qng"
STUDIO_DEV_CACHE = Path(
    r"\\wsl.localhost\Ubuntu\home\ini\.cache\gltest-direct\extracted\vstudio-dev"
)


def _inject_studio_message(vm: VMContext) -> None:
    from genlayer import calldata
    from genlayer.types import Address

    def address(value: Any) -> Address:
        if value is None:
            return Address(b"\x00" * Address.SIZE)
        if isinstance(value, Address):
            return value
        if isinstance(value, bytes):
            return Address(value)
        if hasattr(value, "as_bytes"):
            return Address(value.as_bytes)
        return Address(value)

    sender = address(vm.sender)
    contract = address(vm._contract_address)
    origin = address(vm.origin)
    message = {
        "contract_address": contract,
        "sender_address": sender,
        "origin_address": origin,
        "signer_address": sender,
        "stack": [],
        "value": vm._value,
        "datetime": vm._datetime,
        "is_init": False,
        "chain_id": vm._chain_id,
        "entry_kind": 0,
        "entry_data": b"",
        "entry_stage_data": None,
    }
    encoded = calldata.encode(message)
    fd, path = tempfile.mkstemp()
    try:
        os.write(fd, encoded)
        os.lseek(fd, 0, os.SEEK_SET)
        vm._original_stdin_fd = os.dup(0)
        os.dup2(fd, 0)
    finally:
        os.close(fd)
        try:
            os.unlink(path)
        except PermissionError:
            # Windows keeps fd 0 open until the direct VM refreshes it.
            pass


def _allocate_studio_contract(contract_cls: type, vm: VMContext, *args: Any, **kwargs: Any) -> Any:
    from genlayer.storage import ROOT_SLOT_ID
    from genlayer.storage._internal.generate import (
        ORIGINAL_INIT_ATTR,
        _BuilderCtx,
        _storage_build,
    )

    descriptor = _storage_build(_BuilderCtx.empty(), contract_cls)
    instance = descriptor.get(vm._storage.get_store_slot(ROOT_SLOT_ID), 0)
    init_owner = getattr(descriptor, "cls", None)
    init = getattr(init_owner, "__init__", None) if init_owner is not None else getattr(contract_cls, "__init__", None)
    if init is not None:
        if hasattr(init, ORIGINAL_INIT_ATTR):
            init = getattr(init, ORIGINAL_INIT_ATTR)
        init(instance, *args, **kwargs)
    return instance


def _studio_run_nondet(leader_fn, validator_fn, /, **kwargs):
    from gltest.direct import wasi_mock

    vm = wasi_mock.get_vm()
    vm._in_nondet = True
    try:
        result = leader_fn()
    finally:
        vm._in_nondet = False
    vm._captured_validators.append((result, leader_fn, validator_fn))
    return result


def _studio_run_nondet_lazy(leader_fn, validator_fn, /, **kwargs):
    from genlayer.types import Lazy

    return Lazy(lambda: _studio_run_nondet(leader_fn, validator_fn, **kwargs))


_studio_run_nondet.lazy = _studio_run_nondet_lazy


def _refresh_studio_message(self: VMContext) -> None:
    if "genlayer.message" not in sys.modules:
        return
    from genlayer.types import Address

    def address(value: Any) -> Any:
        if value is None or isinstance(value, Address):
            return value
        if isinstance(value, bytes):
            return Address(value)
        if hasattr(value, "as_bytes"):
            return Address(value.as_bytes)
        return value

    message = sys.modules["genlayer.message"]
    message.sender_address = address(self.sender)
    message.origin_address = address(self.origin)
    message.value = self._value
    message.chain_id = self._chain_id
    message.datetime = self._datetime
    if isinstance(getattr(message, "raw", None), dict):
        message.raw.update(
            sender_address=address(self.sender),
            origin_address=address(self.origin),
            value=self._value,
            chain_id=self._chain_id,
            datetime=self._datetime,
        )


def _run_validator_studio(
    self: VMContext,
    *,
    leader_result: Any = _sentinel,
    leader_error: Exception | None = None,
    index: int = -1,
) -> bool:
    from genlayer import vm as gl_vm

    if not self._captured_validators:
        raise RuntimeError("No validator captured.")
    stored_result, _leader_fn, validator_fn = self._captured_validators[index]
    if leader_error is not None:
        wrapped = gl_vm.UserError(str(leader_error))
    elif leader_result is not _sentinel:
        wrapped = gl_vm.Return(calldata=leader_result)
    else:
        wrapped = gl_vm.Return(calldata=stored_result)
    return validator_fn(wrapped)


direct_loader._inject_message_to_fd0 = _inject_studio_message
direct_loader._allocate_contract = _allocate_studio_contract
direct_loader._patch_run_nondet_for_direct_mode = lambda: _install_studio_patches()
VMContext._refresh_gl_message = _refresh_studio_message
VMContext.run_validator = _run_validator_studio


def _setup_cached_studio_paths(contract_path: Path, sdk_version: str | None = None):
    """Use the already extracted Studio Dev runner instead of network lookup."""
    runner = STUDIO_DEV_CACHE / "py-genlayer" / STUDIO_DEV_RUNNER_HASH
    std_hash = "kzr02ndm9et4qkmbqpq5djjt5sme2yt76n7sz1qbzax0knt6mam0"
    standard = STUDIO_DEV_CACHE / "py-lib-genlayer-std" / std_hash
    paths = [runner, standard]
    for path in reversed(paths):
        import_path = path / "src" if (path / "src").exists() else path
        if str(import_path) not in sys.path:
            sys.path.insert(0, str(import_path))
    return paths


def _install_cached_sdk_loader() -> None:
    import gltest.direct.sdk_loader as sdk_loader

    sdk_loader.setup_sdk_paths = _setup_cached_studio_paths


_install_cached_sdk_loader()


def _install_studio_patches() -> None:
    from gltest.direct import wasi_mock
    import genlayer.nondet as nondet
    import genlayer.nondet.web as web
    import genlayer.vm as vm

    vm.run_nondet = _studio_run_nondet

    def exec_prompt(prompt: str, /, **config):
        active_vm = wasi_mock.get_vm()
        response = active_vm._match_llm_mock(prompt)
        if response is None:
            raise RuntimeError("No LLM mock configured")
        if config.get("response_format", "text") == "json" and isinstance(response, str):
            return json.loads(response)
        return response

    def render(url: str, /, **kwargs):
        active_vm = wasi_mock.get_vm()
        mock = active_vm._match_web_mock(url, "GET")
        if mock is None:
            raise RuntimeError("No web mock configured")
        body = mock.get("body", "")
        if isinstance(body, bytes):
            body = body.decode("utf-8")
        return body

    nondet.exec_prompt = exec_prompt
    web.render = render

    def spawn_sandbox(fn, **kwargs):
        return vm.Return(calldata=fn())

    vm.spawn_sandbox = spawn_sandbox
