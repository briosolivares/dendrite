from app.models import ParsedMessage, SlackEvent
from app.parser import parse_event


def process_slack_event(event: SlackEvent) -> ParsedMessage:
    return parse_event(event)
