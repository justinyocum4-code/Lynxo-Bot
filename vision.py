"""Stub interface for the Phase-2 AI vision check on 18+ selfies.

Phase 1 has no vision provider selected yet, so every photo goes to the
human mod review queue. When a provider is picked, implement
check_selfie() to return "pass" / "fail" and the verification flow will
use it automatically.
"""


class VisionResult:
    def __init__(self, verdict, note=""):
        if verdict not in ("pass", "fail", "review"):
            raise ValueError("verdict must be pass, fail, or review")
        self.verdict = verdict
        self.note = note


async def check_selfie(image_bytes, expected_count):
    """Decide whether a selfie shows `expected_count` raised fingers.

    Returns a VisionResult. Phase 1 always returns "review".
    """
    return VisionResult(
        "review",
        "AI vision is not configured yet — routed to human review.",
    )
