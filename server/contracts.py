from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from server.paths import clean_folder

Decision = Literal["undecided", "keep", "clutter", "later"]
GroupId = Literal[
    "screenshots",
    "memes",
    "events",
    "documents",
    "duplicates",
    "badshots",
    "places",
    "people",
    "other",
    "unclassified",
]
Band = Literal["high", "medium", "low"]
Tri = Literal["yes", "no", "unknown"]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Evidence(Contract):
    source: Literal["rule", "siglip2", "mobileclip2", "yunet", "d3nano"]
    text: str
    strength: Literal["strong", "weak", "small_margin", "not_checked"]


class ProtectedInfo(Contract):
    reasons: list[Literal["faces", "document", "folder", "age", "manual_keep"]]


class Face(Contract):
    box: tuple[float, float, float, float]


class Photo(Contract):
    contentId: str
    filename: str
    paths: list[str]
    thumbUrl: str
    previewUrl: str
    width: int
    height: int
    bytes: int
    format: str
    captureDate: str | None
    captureDateSource: Literal["exif", "filename", "file_time", "unknown"]
    camera: str | None = None
    gps: dict[str, float] | None = None
    origin: Literal[
        "camera", "screenshot", "forwarded", "downloaded", "edited", "unknown"
    ]
    groupId: GroupId
    groupSource: Literal["ai", "manual"]
    band: Band
    triage: Literal[
        "likely_keep", "clutter_candidate", "needs_review", "awaiting_analysis"
    ]
    decision: Decision
    decisionVersion: int
    reviewVersion: int
    fileState: Literal["available", "unsupported", "missing", "unreadable"]
    protected: ProtectedInfo | None
    sensitive: Tri
    importance: Tri
    faces: list[Face] | Literal["not_checked"]
    evidence: list[Evidence]
    duplicateSetId: str | None = None
    eventId: str | None = None
    documentType: (
        Literal[
            "Papers", "Receipts and bills", "IDs", "Notes and whiteboards", "Slides"
        ]
        | None
    ) = None
    blurRequired: bool


class PhotoQuery(Contract):
    root: str | None = None
    folder: str = ""
    group: GroupId | None = None
    event: str | None = None
    documentType: (
        Literal[
            "Papers", "Receipts and bills", "IDs", "Notes and whiteboards", "Slides"
        ]
        | None
    ) = None
    band: Literal["all", "high", "medium", "low"] = "all"
    origin: Literal[
        "all", "camera", "screenshot", "forwarded", "downloaded", "edited", "unknown"
    ] = "all"
    format: str = "all"
    q: str = Field(default="", max_length=1000)
    protectedOnly: bool = False
    faces: bool = False
    duplicate: bool = False
    decisions: Literal["undecided", "all", "keep", "clutter", "later"] = "undecided"
    reviewed: bool = False
    sort: Literal["default", "margin", "date", "size", "name"] = "default"
    by: Literal["none", "folder", "month", "event"] = "none"
    page: int = Field(default=1, ge=1)
    limit: int = Field(default=180, ge=1, le=500)
    revision: int | None = None

    @model_validator(mode="after")
    def scope(self):
        self.folder = clean_folder(self.folder)
        if self.folder and not self.root:
            raise ValueError("A folder requires an explicit root")
        return self


class RootInput(Contract):
    path: str
    name: str = Field(min_length=1, max_length=200)


class InventoryQuery(PhotoQuery):
    state: Literal["available", "unsupported", "missing", "unreadable"] | None = None


class DuplicateQuery(PhotoQuery):
    kind: Literal["all", "exact", "possible", "burst"] = "all"


class SearchQuery(PhotoQuery):
    like: str | None = None


class ScanInput(Contract):
    folder: str = ""
    verify: bool = False


class SelectionInput(Contract):
    query: PhotoQuery
    queryRevision: int
    queryFingerprint: str
    exclusions: list[str] = Field(default_factory=list, max_length=100000)
    contentIds: list[str] | None = Field(default=None, max_length=100000)
    includeProtected: bool = False


class BulkInput(Contract):
    selectionId: str
    decision: Decision


class PhotoPatch(Contract):
    expectedVersion: int = Field(ge=0)
    decision: Decision | None = None
    groupId: GroupId | None = None
    removeFromEvent: bool = False
    overrideProtected: bool = False

    @model_validator(mode="after")
    def one_operation(self):
        if (
            sum(
                [
                    self.decision is not None,
                    self.groupId is not None,
                    self.removeFromEvent,
                ]
            )
            != 1
        ):
            raise ValueError("Specify one review operation")
        return self


class EventInput(Contract):
    name: str = Field(min_length=1, max_length=200)
    contentIds: list[str] = Field(default_factory=list)


class EventPatch(Contract):
    expectedVersion: int
    name: str | None = Field(default=None, min_length=1, max_length=200)
    status: Literal["suggested", "confirmed"] | None = None


class MergeInput(Contract):
    intoId: str
    expectedVersion: int
    targetVersion: int


class SplitInput(Contract):
    expectedVersion: int
    contentIds: list[str] = Field(min_length=1)
    name: str = Field(min_length=1, max_length=200)


class ExportInput(Contract):
    query: PhotoQuery
    decisions: list[Literal["keep", "later", "clutter"]] = Field(
        default_factory=lambda: ["keep"], min_length=1
    )
    groups: list[GroupId] = Field(default_factory=list)
    includeSensitive: bool = False
    destination: str
    template: Literal["year-month", "year-event", "original"] = "year-month"


class ApprovalInput(Contract):
    revision: int
    fingerprint: str


class SettingsInput(Contract):
    expectedVersion: int
    value: dict


class ProtectionSettings(Contract):
    protectedFolders: list[dict[str, str]] = Field(default_factory=list)
    ageBefore: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")


class AppearanceSettings(Contract):
    theme: Literal["dark", "light", "system"] = "dark"
    density: Literal["compact", "comfortable"] = "compact"
    thumbnailSize: Literal["small", "medium", "large"] = "medium"
    autoAdvance: bool = False


class ClaimInput(Contract):
    owner: str = Field(min_length=1, max_length=200)
    stage: Literal["prepare", "embed", "assess"]
    fingerprint: str = Field(min_length=1, max_length=2000)
    leaseSeconds: int = Field(default=120, ge=10, le=600)


class LeaseInput(Contract):
    owner: str
    token: str
    attempt: int


class ResultInput(LeaseInput):
    fingerprint: str
    sha256: str
    payload: dict


class FailureInput(LeaseInput):
    state: Literal["failed", "paused"]
    reason: str = Field(min_length=1, max_length=1000)
