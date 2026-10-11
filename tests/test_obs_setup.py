from types import SimpleNamespace as Data
from unittest.mock import Mock, patch

import pytest

from obs_setup import SCENE, SOURCE, install_output


def client(existing=True, extra=False, url="http://127.0.0.1:8791/owned"):
    result = Mock()
    for method in ("get_stream_status", "get_record_status", "get_virtual_cam_status"):
        getattr(result, method).return_value = Data(output_active=False)
    result.get_scene_list.return_value = Data(current_program_scene_name=SCENE,
                                             scenes=[{"sceneName": SCENE}] if existing else [])
    result.get_input_list.return_value = Data(inputs=[{"inputName": SOURCE, "inputKind": "browser_source"}] if existing else [])
    result.get_input_settings.return_value = Data(input_kind="browser_source", input_settings={"url": url})
    items = [{"sourceName": SOURCE}] + ([{"sourceName": "Raw Display"}] if extra else [])
    result.get_scene_item_list.return_value = Data(scene_items=items)
    result.get_scene_item_id.return_value = Data(scene_item_id=1)
    result.create_input.return_value = Data(scene_item_id=1)
    result.get_video_settings.return_value = Data(base_width=1920, base_height=1080)
    return result


def owner():
    return {"url": "http://127.0.0.1:8791/owned", "obs": {"scene": SCENE, "source": SOURCE, "previous_scene": "Scene"}}


@pytest.mark.parametrize("extra,url,prior", [
    (False, "http://127.0.0.1:9999/someone-else", owner()),
    (True, "http://127.0.0.1:8791/owned", owner()),
    (False, "http://127.0.0.1:8791/owned", {}),
])
def test_refused_ownership_leaves_obs_unmodified(extra, url, prior):
    control = client(extra=extra, url=url)
    with patch("obs_setup.connect", return_value=control), pytest.raises(RuntimeError):
        install_output("http://127.0.0.1:8791/new", (1440, 900), prior)
    assert not any(call[0].startswith(("set_", "create_")) for call in control.method_calls)
    control.disconnect.assert_called_once()


def test_owned_restart_preserves_original_rollback_scene():
    control = client()
    with patch("obs_setup.connect", return_value=control):
        receipt = install_output("http://127.0.0.1:8791/owned", (1440, 900), owner())
    assert receipt["previous_scene"] == "Scene"
    control.set_current_program_scene.assert_called_once_with(SCENE)
    control.create_scene.assert_not_called()


def test_active_broadcast_is_preserved():
    control = client()
    control.get_stream_status.return_value.output_active = True
    with patch("obs_setup.connect", return_value=control), pytest.raises(RuntimeError):
        install_output("http://127.0.0.1:8791/owned", (1440, 900), owner())
    assert not any(call[0].startswith(("set_", "create_")) for call in control.method_calls)
