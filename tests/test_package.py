from livekit.plugins import voicebox


def test_namespace_import():
    assert voicebox.__version__ == "0.1.0.dev0"
