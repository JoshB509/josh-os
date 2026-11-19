"""Contract for a dedicated, host-controlled Kit image adapter.

kit_chrome_browser provides a supported read-only implementation. Its upload capability
remains unavailable until current Kit UI and persistence evidence can be verified.

The model never supplies observations, receipts, authorization, selectors, paths or URLs.
Implementations must use supported visible Kit editor controls only, never hidden APIs,
arbitrary JS execution or scheduling/send controls. The general Laia Chrome tool list is
not an implementation of this contract. Injecting an adapter is privileged host code.
"""
from dataclasses import dataclass
from typing import Callable, Protocol


class BrowserUnavailable(ValueError):
    pass


@dataclass(frozen=True)
class Snapshot:
    capture_id: str
    captured_at: str
    url: str
    reloaded: bool
    metadata: dict
    content_html: str
    # Current src -> trusted Media Gallery/upload resource identity, read from UI.
    image_resources: dict
    api_broadcast: dict | None = None  # raw read-only get_broadcast, never a model summary
    api_unavailable_reason: str = ''
    content_proof: dict | None = None  # supported UI/API observation, never model claims


@dataclass(frozen=True)
class UploadReceipt:
    packet_id: str
    ordinal: int
    png_hash: str
    resource_id: str
    observed_src: str
    method: str
    evidence_id: str  # trusted adapter audit artifact, never model prose


@dataclass(frozen=True)
class InsertCommand:
    packet_id: str
    packet_digest: str
    broadcast_id: int
    destination: str
    method: str
    ordinal: int
    filename: str
    png_hash: str
    alt: str
    anchor: dict


class KitVisualBrowser(Protocol):
    def readiness(self) -> dict: ...

    def inspect(self, broadcast_id: int, *, reload: bool) -> Snapshot: ...

    def insert_image(self, command: InsertCommand, png: bytes,
                     before_mutation: Callable[[str, Snapshot], None],
                     record_upload: Callable[[UploadReceipt], None]) -> None:
        """One exact image, no other action. Never retries a mutation internally.

        Call before_mutation immediately before each mutating UI phase with a fresh
        observation of this draft: media_gallery phases 'upload', then 'insert';
        media_gallery_auto phase 'upload_insert' for Kit's combined operation;
        clipboard phase 'paste'. The auto-insert recipe must bind and recheck its
        subsequent caption/alt controls on the same selected original image, and
        must fail closed on any intermediate difference or uncertain write.
        A denied callback MUST stop the operation. Record
        the trusted upload source as soon as known, before insertion for Media Gallery
        and immediately after paste for clipboard. No generic click/navigation channel
        is exposed to the agent. The eventual live implementation must enforce this
        in code and prove it against current Kit UI; this Protocol alone is not a sandbox.
        """
        ...


class UnavailableKitVisualBrowser:
    def readiness(self):
        return dict(available=False, methods=[], reason=(
            'No constrained Kit visual browser adapter is installed or live-verified. '
            'Local preview is not approval to upload. No browser action is available.'))

    def inspect(self, broadcast_id, *, reload):
        raise BrowserUnavailable(self.readiness()['reason'])

    def insert_image(self, *args, **kwargs):
        raise BrowserUnavailable(self.readiness()['reason'])
