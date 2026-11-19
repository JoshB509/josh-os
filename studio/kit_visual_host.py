"""Single-owner CareerOS wiring. Does not give any chat agent a browser capability."""
from .carousel_counts import DEVICE
from .kit_chrome_browser import KitChromeBrowser
from .kit_visual_insertions import KitVisualInsertions
from .kit_visual_review import KitVisualReview


def wire(server):
    browser=KitChromeBrowser(server.root,server.store.root/'kit-visual-browser',device_id=DEVICE)
    service=KitVisualInsertions(server.emails,server.kit_drafts,owner_id='career-os-owner',browser=browser)
    return KitVisualReview(service)
