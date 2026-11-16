"""Memory headroom regression without removing LSP ownership limits."""

from tools.language import sessions


def test_resource_headroom_retains_global_and_per_session_caps() -> None:
    mib = 1024 * 1024
    assert 768 * mib <= sessions._MAX_RSS_BYTES <= 1024 * mib
    assert 2 * 1024 * mib <= sessions._MAX_TOTAL_RSS_BYTES <= 3 * 1024 * mib
    assert sessions._MAX_TOTAL_RSS_BYTES >= 3 * sessions._MAX_RSS_BYTES
    assert sessions._MAX_HANDLES_PER_SESSION >= 768
    assert sessions._MAX_TYPESCRIPT_HANDLES_PER_SESSION >= 1536
    assert sessions._MAX_DOCUMENT_BYTES <= sessions._MAX_DOCUMENT_TOTAL_BYTES
    assert sessions._MAX_DOCUMENT_TOTAL_BYTES <= sessions._MAX_RSS_BYTES // 8
