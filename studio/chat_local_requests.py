"""Small current-request checks, never used to authorize external writes."""
import re

def requested_read(message):
    text = str(message or '').strip().lower()
    if re.search(r"\b(?:don't|do not|never|stop|cancel)\b", text):
        return False
    return bool(re.search(r'\b(?:check|refresh|look up|lookup|read|find|update|show)\b', text))


def bind_target(message, target_id, contexts, context=None, noun='project'):
    """Resolve only exact unique saved titles or the host's actual open-item context."""
    text = str(message or '')
    if not target_id or target_id not in contexts:
        return text
    if target_id in text:
        return text
    title = str(contexts[target_id].get('title') or contexts[target_id].get('name') or '')
    matches = [key for key,row in contexts.items() if title and str(row.get('title') or row.get('name') or '').casefold() == title.casefold()]
    if len(matches)==1 and title.casefold() in text.casefold():
        return re.sub(re.escape(title), lambda m: target_id, text, flags=re.I)
    if (context or {}).get('id')==target_id:
        return re.sub(r'\bthis '+re.escape(noun)+r'\b', lambda m:target_id, text, flags=re.I)
    return text
