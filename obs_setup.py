"""Install an owned safe output scene without starting any OBS output."""
import json
import logging
import time
from pathlib import Path

SCENE = "Safestream"
SOURCE = "Safestream Redacted Output"


def connect():
    import obsws_python as obs
    logging.getLogger("obsws_python").setLevel(logging.CRITICAL)
    config = Path.home() / "Library/Application Support/obs-studio/plugin_config/obs-websocket/config.json"
    settings = json.loads(config.read_text())
    if not settings.get("server_enabled"):
        raise RuntimeError("OBS websocket unavailable")
    for attempt in range(12):
        try:
            return obs.ReqClient(host="127.0.0.1", port=settings.get("server_port", 4455),
                                 password=settings.get("server_password", ""), timeout=4)
        except (ConnectionRefusedError, OSError):
            if attempt == 11:
                raise RuntimeError("OBS is not ready") from None
            time.sleep(0.5)


def install_output(url, size, prior=None, on_plan=None):
    client = connect()
    try:
        if (client.get_stream_status().output_active or client.get_record_status().output_active
                or client.get_virtual_cam_status().output_active):
            raise RuntimeError("OBS output already active; preserve its scene")
        scenes = client.get_scene_list()
        prior = prior or {}
        previous = prior.get("obs", {}).get("previous_scene", scenes.current_program_scene_name)
        exists = SCENE in [x["sceneName"] for x in scenes.scenes]
        inputs = {x["inputName"]: x["inputKind"] for x in client.get_input_list().inputs}
        # Check the entire ownership boundary before changing any existing object.
        if exists or SOURCE in inputs:
            owner = prior.get("obs", {})
            if not exists or owner.get("scene") != SCENE or owner.get("source") != SOURCE:
                raise RuntimeError("Existing OBS objects have another owner")
            items = client.get_scene_item_list(SCENE).scene_items
            if SOURCE in inputs:
                old = client.get_input_settings(SOURCE)
                if (old.input_kind != "browser_source" or old.input_settings.get("url") != prior.get("url")
                        or len(items) != 1 or items[0]["sourceName"] != SOURCE):
                    raise RuntimeError("Existing OBS scene differs from saved ownership")
            elif owner.get("phase") != "creating" or items:
                raise RuntimeError("Existing OBS scene lacks owned output")
        settings = {"url": url, "width": size[0], "height": size[1],
                    "is_local_file": False, "shutdown": False, "restart_when_active": False,
                    "fps_custom": True, "fps": 10, "webpage_control_level": 0}
        if SOURCE in inputs:
            if any(old.input_settings.get(key) != value for key, value in settings.items()):
                client.set_input_settings(SOURCE, settings, True)
            item = client.get_scene_item_id(SCENE, SOURCE).scene_item_id
        else:
            if on_plan:
                on_plan({"scene": SCENE, "source": SOURCE, "previous_scene": previous, "phase": "creating"})
            if not exists:
                client.create_scene(SCENE)
            item = client.create_input(SCENE, SOURCE, "browser_source", settings, True).scene_item_id
        items = client.get_scene_item_list(SCENE).scene_items
        if len(items) != 1 or items[0]["sourceName"] != SOURCE:
            raise RuntimeError("Safestream scene contains additional sources")
        video = client.get_video_settings()
        client.set_scene_item_transform(SCENE, item, {
            "positionX": video.base_width / 2, "positionY": video.base_height / 2,
            "alignment": 0, "boundsType": "OBS_BOUNDS_SCALE_INNER",
            "boundsWidth": video.base_width, "boundsHeight": video.base_height,
        })
        client.set_scene_item_enabled(SCENE, item, True)
        client.set_current_program_scene(SCENE)
        return {"scene": SCENE, "source": SOURCE, "previous_scene": previous,
                "canvas": [video.base_width, video.base_height]}
    finally:
        client.disconnect()
