from app.models import ParsedMessage, SlackEvent


def parse_event(event: SlackEvent) -> ParsedMessage:
    entities = [token for token in event.text.split() if token.startswith("#")]
    summary = event.text[:120].strip()
    return ParsedMessage(summary=summary, entities=entities)
