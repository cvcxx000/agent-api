"""UI 的 JSON 接口：提供商管理、运行时切模型、设置、系统提示词、数据备份。"""

from __future__ import annotations

import os


def mount(router) -> None:
    """把 UI 路由挂到服务上（页面自己带 X-API-Token 调接口）。"""
    import io
    import zipfile
    from dataclasses import replace

    from . import providers as provider_store
    from .errors import AppError
    from .llm import build_llm
    from .prompts import PROMPT_FILE
    from .server import Response

    def _apply(runtime, resolved: dict) -> dict:
        """把某家提供商的 base/model/key 装进内存 settings，并重建客户端。"""
        st = runtime.settings
        key = resolved.get("api_key") or ""
        new = replace(
            st,
            llm_base_url=resolved["base_url"],
            llm_model=resolved["model"],
            llm_api_key=key,
            llm_mock="false" if key else "auto",
        )
        runtime.settings = new
        runtime.llm = build_llm(new)
        return {
            "model": new.llm_model,
            "base_url": new.llm_base_url,
            "mock_llm": new.use_mock_llm,
            "provider": resolved.get("id", ""),
        }

    def ui_llm(req, params):
        """手动覆盖模型：只改内存里的 settings + 重建 llm，不写 .env。"""
        runtime = req.runtime
        st = runtime.settings
        body = req.json()

        changes: dict = {}
        base_url = body.get("base_url")
        if isinstance(base_url, str) and base_url.strip():
            changes["llm_base_url"] = base_url.strip().rstrip("/")
        model = body.get("model")
        if isinstance(model, str) and model.strip():
            changes["llm_model"] = model.strip()
        api_key = body.get("api_key")
        if isinstance(api_key, str) and api_key.strip():
            changes["llm_api_key"] = api_key.strip()
        rate_limit = body.get("rate_limit")
        if rate_limit is not None and str(rate_limit).strip() != "":
            os.environ["RATE_LIMIT_PER_MIN"] = str(rate_limit).strip()
        mock = body.get("mock")
        if mock is True:
            changes["llm_mock"] = "true"
        elif mock is False and (changes.get("llm_api_key") or st.llm_api_key):
            changes["llm_mock"] = "false"

        st = replace(st, **changes) if changes else st
        runtime.settings = st
        runtime.llm = build_llm(st)
        return Response(
            200,
            {
                "ok": True,
                "model": st.llm_model,
                "base_url": st.llm_base_url,
                "mock_llm": st.use_mock_llm,
                "rate_limit_per_min": os.getenv("RATE_LIMIT_PER_MIN", ""),
            },
        )

    # ---------------- 提供商 ----------------
    def providers_list(req, params):
        view = provider_store.public(req.runtime.settings)
        view["current"] = {"model": req.runtime.settings.llm_model, "base_url": req.runtime.settings.llm_base_url}
        return Response(200, view)

    def providers_upsert(req, params):
        body = req.json()
        return Response(200, provider_store.upsert(req.runtime.settings, body))

    def provider_delete(req, params):
        return Response(200, provider_store.remove(req.runtime.settings, params["pid"]))

    def provider_toggle(req, params):
        body = req.json()
        return Response(200, provider_store.toggle(req.runtime.settings, params["pid"], bool(body.get("enabled")))

    def provider_add_model(req, params):
        body = req.json()
        model = body.get("model") or body.get("id") or ""
        return Response(200, provider_store.add_model(req.runtime.settings, params["pid"], model))

    def provider_del_model(req, params):
        return Response(200, provider_store.remove_model(req.runtime.settings, params["pid"], params["model"]))

    def provider_activate(req, params):
        body = req.json()
        runtime = req.runtime
        resolved = provider_store.activate(runtime.settings, params["pid"], str(body.get("model") or ""))
        applied = _apply(runtime, resolved)
        view = provider_store.public(runtime.settings)
        view.update(applied)
        view["ok"] = True
        return Response(200, view)

    def provider_test(req, params):
        body = req.json()
        runtime = req.runtime
        resolved = provider_store.resolve(runtime.settings, params["pid"], str(body.get("model") or ""))
        if not resolved["api_key"]:
            raise AppError(f"{resolved['name']} 还没填 API Key，先在详情里保存 Key")
        temp = replace(
            runtime.settings,
            llm_base_url=resolved["base_url"],
            llm_model=resolved["model"],
            llm_api_key=resolved["api_key"],
            llm_mock="false",
        )
        client = build_llm(temp)
        messages = [{"role": "user", "content": str(body.get("prompt") or "只回复两个字：收到")}]
        result = client.chat(messages, tools=None, temperature=0.0, max_tokens=64)
        return Response(
            200,
            {
                "ok": True,
                "provider": resolved["id"],
                "model": resolved["model"],
                "reply": str(result.get("content") or "")[:500],
            },
        )

    # ---------------- 设置页 ----------------
    def ui_settings(req, params):
        runtime = req.runtime
        st = runtime.settings
        return Response(
            200,
            {
                "model": st.llm_model,
                "base_url": st.llm_base_url,
                "mock_llm": st.use_mock_llm,
                "host": st.host,
                "port": st.port,
                "auth_enabled": st.auth_enabled,
                "rate_limit_per_min": os.getenv("RATE_LIMIT_PER_MIN", "7"),
                "max_turns": st.max_turns,
                "max_subagents": st.max_subagents,
                "max_subagent_depth": st.max_subagent_depth,
                "sandbox_root": str(st.sandbox_root),
                "data_dir": str(st.data_dir),
                "skills_dirs": [str(p) for p in st.skills_dirs],
                "workflows_dir": str(st.workflows_dir),
                "enable_python": st.enable_python,
                "enable_shell": st.enable_shell,
                "python_timeout": st.python_timeout,
                "prompt_file": str(PROMPT_FILE),
                "prompt_chars": len(runtime.default_system_prompt or ""),
            },
        )

    def ui_prompt_get(req, params):
        return Response(200, {"text": req.runtime.default_system_prompt or ""})

    def ui_prompt_set(req, params):
        body = req.json()
        text = body.get("text")
        if not isinstance(text, str):
            raise AppError("text 必须是字符串")
        text = text.strip()
        runtime = req.runtime
        target = runtime.settings.data_dir / "default_system.md"
        if text:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text + "\n", encoding="utf-8")
            runtime.default_system_prompt = text
        else:
            if target.exists():
                target.unlink()
            from .prompts import load_default_system_prompt

            runtime.default_system_prompt = load_default_system_prompt(runtime.settings)
        return Response(200, {"ok": True, "chars": len(runtime.default_system_prompt), "file": str(target)})

    def ui_prompt_reset(req, params):
        runtime = req.runtime
        target = runtime.settings.data_dir / "default_system.md"
        if target.exists():
            target.unlink()
        from .prompts import load_default_system_prompt

        runtime.default_system_prompt = load_default_system_prompt(runtime.settings)
        return Response(200, {"ok": True, "chars": len(runtime.default_system_prompt)})

    def backup(req, params):
        st = req.runtime.settings
        buf = io.BytesIO()
        root = st.data_dir
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("README.txt", "agent-api 数据备份：解压后覆盖回 data/ 目录，重启服务即恢复。" + "\n")
            if root.exists():
                for path in root.rglob("*"):
                    if path.is_dir() or path.suffix == ".zip":
                        continue
                    rel = path.relative_to(root)
                    if rel.parts and rel.parts[0] == "tmp":
                        continue
                    zf.write(path, "data/" + str(rel))
        payload = buf.getvalue()
        return Response(
            200,
            payload,
            headers={"Content-Disposition": 'attachment; filename="agent-api-backup.zip"'},
            content_type="application/zip",
        )

    router.add("POST", "/api/ui/llm", ui_llm, auth=True)
    router.add("GET", "/api/providers", providers_list, auth=True)
    router.add("POST", "/api/providers", providers_upsert, auth=True)
    router.add("DELETE", "/api/providers/{pid}", provider_delete, auth=True)
    router.add("POST", "/api/providers/{pid}/toggle", provider_toggle, auth=True)
    router.add("POST", "/api/providers/{pid}/models", provider_add_model, auth=True)
    router.add("DELETE", "/api/providers/{pid}/models/{model}", provider_del_model, auth=True)
    router.add("POST", "/api/providers/{pid}/activate", provider_activate, auth=True)
    router.add("POST", "/api/providers/{pid}/test", provider_test, auth=True)
    router.add("GET", "/api/ui/settings", ui_settings, auth=True)
    router.add("GET", "/api/ui/prompt", ui_prompt_get, auth=True)
    router.add("POST", "/api/ui/prompt", ui_prompt_set, auth=True)
    router.add("DELETE", "/api/ui/prompt", ui_prompt_reset, auth=True)
    router.add("GET", "/api/backup", backup, auth=True)
