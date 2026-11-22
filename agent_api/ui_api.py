"""UI 的 JSON 接口：提供商管理、运行时切模型、设置、系统提示词、数据备份。"""

from __future__ import annotations

import os


def mount(router) -> None:
    """把 UI 路由挂到服务上（页面自己带 X-API-Token 调接口）。"""
    import io
    import zipfile
    from dataclasses import replace
    from pathlib import Path

    from . import media as media_store
    from . import tts as tts_store
    from . import providers as provider_store
    from . import search as search_store
    from .errors import AppError, NotFoundError
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
        meta = provider_store.active_meta(req.runtime.settings)
        if meta.get("id"):
            view["current"] = {"model": meta["model"], "base_url": meta["base_url"], "provider": meta["name"]}
        else:
            view["current"] = {"model": "", "base_url": "", "provider": ""}
        return Response(200, view)

    def providers_upsert(req, params):
        body = req.json()
        return Response(200, provider_store.upsert(req.runtime.settings, body))

    def provider_delete(req, params):
        return Response(200, provider_store.remove(req.runtime.settings, params["pid"]))

    def provider_toggle(req, params):
        body = req.json()
        return Response(200, provider_store.toggle(req.runtime.settings, params["pid"], bool(body.get("enabled"))))

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

    # ---------------- 目录 / 拉模型 / 清空壳 ----------------
    def providers_catalog(req, params):
        return Response(200, {"providers": provider_store.catalog()})

    def provider_add(req, params):
        body = req.json()
        runtime = req.runtime
        view = provider_store.add_from_preset(
            runtime.settings,
            str(body.get("preset") or body.get("id") or ""),
            str(body.get("api_key") or ""),
            str(body.get("base_url") or ""),
            str(body.get("name") or ""),
            str(body.get("model") or ""),
        )
        model = str(body.get("model") or "").strip()
        if model and view.get("added"):
            resolved = provider_store.activate(runtime.settings, view["added"], model)
            view.update(_apply(runtime, resolved))
        view["ok"] = True
        return Response(200, view)

    def provider_fetch_models(req, params):
        runtime = req.runtime
        fetched = provider_store.fetch_models(runtime.settings, params["pid"])
        view = provider_store.public(runtime.settings)
        view["fetched"] = fetched
        return Response(200, view)

    def provider_vision(req, params):
        body = req.json()
        return Response(200, provider_store.set_vision(req.runtime.settings, params["pid"], bool(body.get("vision"))))

    def providers_prune(req, params):
        return Response(200, provider_store.prune_empty(req.runtime.settings))

    # ---------------- 图片 / 视频生成配置 ----------------
    def ui_media(req, params):
        return Response(200, media_store.public(req.runtime.settings))

    def ui_media_set(req, params):
        body = req.json()
        return Response(200, media_store.configure(req.runtime.settings, params["kind"], body))

    def ui_media_generate(req, params):
        body = req.json()
        kind = params["kind"]
        settings = req.runtime.settings
        if kind == "image":
            result = media_store.generate_image(
                settings, str(body.get("prompt") or ""), str(body.get("size") or ""), str(body.get("model") or "")
            )
        elif kind == "video":
            result = media_store.generate_video(
                settings,
                str(body.get("prompt") or ""),
                int(body["seconds"]) if str(body.get("seconds") or "").strip() else None,
                str(body.get("model") or ""),
                int(body.get("timeout") or 420),
            )
        else:
            raise AppError("kind 只能是 image 或 video")
        return Response(200, {"ok": True, "result": result})

    # ---------------- 搜索配置 ----------------
    def ui_search(req, params):
        return Response(200, search_store.public(req.runtime.settings))

    def ui_search_set(req, params):
        return Response(200, search_store.configure(req.runtime.settings, req.json()))

    def ui_search_test(req, params):
        body = req.json()
        count = body.get("count")
        result = search_store.run(
            req.runtime.settings,
            str(body.get("query") or "今天的日期"),
            int(count) if str(count or "").strip() else 3,
        )
        return Response(200, {"ok": True, **result})

    # ---------------- 上传 / 预览 ----------------
    def ui_upload(req, params):
        import base64
        import re as _re
        import time as _time

        body = req.json()
        data_url = str(body.get("data_url") or "")
        if not data_url.startswith("data:") or "," not in data_url:
            raise AppError("data_url 必须是 data:<mime>;base64,<内容>")
        header, _, payload = data_url.partition(",")
        mime = header[5:].split(";")[0].strip().lower()
        if ";base64" not in header:
            raise AppError("只支持 base64 数据")
        try:
            raw = base64.b64decode(payload, validate=False)
        except Exception as exc:  # noqa: BLE001
            raise AppError(f"base64 解不开：{exc}") from exc
        if not raw:
            raise AppError("内容为空")
        if len(raw) > 8 * 1024 * 1024:
            raise AppError("文件超过 8 MB")
        suffix = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp", "image/gif": ".gif"}.get(mime, ".bin")
        if suffix == ".bin":
            raise AppError(f"只收图片（png/jpeg/webp/gif），收到 {mime or '未知'}")
        settings = req.runtime.settings
        target_dir = Path(settings.sandbox_root) / "uploads"
        target_dir.mkdir(parents=True, exist_ok=True)
        stem = _re.sub(r"[^A-Za-z0-9_-]+", "-", str(body.get("name") or "paste")).strip("-")[:24] or "paste"
        name = f"{_time.strftime('%Y%m%d-%H%M%S')}-{stem}{suffix}"
        target = target_dir / name
        target.write_bytes(raw)
        rel = target.relative_to(Path(settings.sandbox_root)).as_posix()
        return Response(
            200,
            {"ok": True, "path": rel, "url": "/files/" + rel, "bytes": len(raw), "type": "image", "suffix": suffix},
        )

    def ui_preview(req, params):
        rel = str(req.q("path") or "").strip()
        if not rel:
            raise AppError("path 不能为空")
        settings = req.runtime.settings
        root = Path(settings.sandbox_root).resolve()
        target = (root / rel).resolve()
        try:
            target.relative_to(root)
        except ValueError:
            raise AppError("路径越出沙箱") from None
        if not target.is_file():
            raise NotFoundError(f"文件不存在：{rel}")
        size = target.stat().st_size
        if size > 2 * 1024 * 1024:
            raise AppError(f"文件太大（{size / 1024:.0f} KB），先截断再预览")
        text = target.read_text(encoding="utf-8", errors="replace")
        return Response(200, {"path": rel, "suffix": target.suffix.lower(), "content": text, "bytes": size})

    # ---------------- 设置页 ----------------
    def ui_settings(req, params):
        runtime = req.runtime
        st = runtime.settings
        meta = provider_store.active_meta(st)
        return Response(
            200,
            {
                "model": (meta.get("model") if meta.get("id") else ""),
                "base_url": (meta.get("base_url") if meta.get("id") else ""),
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
                "media": media_store.public(st),
                "search": search_store.public(st),
                "tts": tts_store.public(st),
                "schedules": len(runtime.schedules.list()) if runtime.schedules else 0,
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
    router.add("GET", "/api/providers/catalog", providers_catalog, auth=True)
    router.add("POST", "/api/providers", providers_upsert, auth=True)
    router.add("POST", "/api/providers/add", provider_add, auth=True)
    router.add("POST", "/api/providers/prune", providers_prune, auth=True)
    router.add("DELETE", "/api/providers/{pid}", provider_delete, auth=True)
    router.add("POST", "/api/providers/{pid}/toggle", provider_toggle, auth=True)
    router.add("POST", "/api/providers/{pid}/models", provider_add_model, auth=True)
    router.add("DELETE", "/api/providers/{pid}/models/{model}", provider_del_model, auth=True)
    router.add("POST", "/api/providers/{pid}/activate", provider_activate, auth=True)
    router.add("POST", "/api/providers/{pid}/test", provider_test, auth=True)
    router.add("POST", "/api/providers/{pid}/fetch-models", provider_fetch_models, auth=True)
    router.add("POST", "/api/providers/{pid}/vision", provider_vision, auth=True)
    router.add("GET", "/api/ui/settings", ui_settings, auth=True)
    router.add("GET", "/api/ui/prompt", ui_prompt_get, auth=True)
    router.add("POST", "/api/ui/prompt", ui_prompt_set, auth=True)
    router.add("DELETE", "/api/ui/prompt", ui_prompt_reset, auth=True)
    # ---------------- 语音合成（TTS） ----------------
    def ui_tts(req, params):
        return Response(200, tts_store.public(req.runtime.settings))

    def ui_tts_set(req, params):
        body = req.json()
        return Response(200, tts_store.configure(req.runtime.settings, body))

    def ui_tts_speak(req, params):
        body = req.json()
        result = tts_store.speak(
            req.runtime.settings,
            str(body.get("text") or ""),
            voice=str(body.get("voice") or ""),
            speed=body.get("speed"),
            fmt=str(body.get("format") or ""),
        )
        return Response(200, {"ok": True, **result})

    router.add("GET", "/api/ui/tts", ui_tts, auth=True)
    router.add("POST", "/api/ui/tts", ui_tts_set, auth=True)
    router.add("POST", "/api/ui/tts/speak", ui_tts_speak, auth=True)

    router.add("GET", "/api/ui/media", ui_media, auth=True)
    router.add("POST", "/api/ui/media/{kind}", ui_media_set, auth=True)
    router.add("POST", "/api/ui/media/{kind}/generate", ui_media_generate, auth=True)
    router.add("GET", "/api/ui/search", ui_search, auth=True)
    router.add("POST", "/api/ui/search", ui_search_set, auth=True)
    router.add("POST", "/api/ui/search/test", ui_search_test, auth=True)
    router.add("POST", "/api/ui/upload", ui_upload, auth=True)
    router.add("GET", "/api/ui/preview", ui_preview, auth=True)
    router.add("GET", "/api/backup", backup, auth=True)
