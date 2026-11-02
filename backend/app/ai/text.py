"""Cắt text trước khi gửi AI (spec 28 mục 5)."""


def cut_text(text: str, limit: int) -> tuple[str, bool]:
    """(phần gửi đi, có bị cắt).

    Cắt ở ranh giới đoạn, rồi cuối dòng, trong nửa sau giới hạn (ranh giới quá sớm bỏ mất phần
    lớn mail); không có thì cắt tại giới hạn.
    """
    if len(text) <= limit:
        return text, False
    for separator in ("\n\n", "\n"):
        at = text.rfind(separator, limit // 2, limit + 1)
        if at != -1:
            return text[:at].rstrip(), True
    return text[:limit].rstrip(), True
