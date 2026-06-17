from dataclasses import asdict, dataclass
from typing import Optional


@dataclass
class Chapter:
    title: str
    content: str
    chapter_number: int
    word_count: int
    filename: Optional[str] = None

    def to_dict(self) -> dict:
        # ponytail: asdict preserves content; old form silently dropped it.
        return asdict(self)


if __name__ == "__main__":
    c = Chapter("T", "body", 1, 2, "f.txt")
    assert c.to_dict() == {
        "title": "T", "content": "body", "chapter_number": 1,
        "word_count": 2, "filename": "f.txt",
    }
    print("ok")
