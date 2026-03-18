"""
Utility helpers for the tickets app.
"""

from django.db.models import F


def render_canned_response(response, ticket, agent):
    """
    Replace template variables in a canned response's content.

    Supported variables:
        {{ticket.number}}, {{ticket.subject}},
        {{contact.name}}, {{contact.first_name}}, {{contact.email}},
        {{agent.name}}, {{agent.first_name}}, {{agent.email}}

    Side-effect: increments ``response.usage_count`` atomically.

    Returns:
        str: The rendered content with variables replaced.
    """
    content = response.content

    # Ticket variables
    content = content.replace("{{ticket.number}}", str(ticket.number))
    content = content.replace("{{ticket.subject}}", ticket.subject)

    # Contact variables
    if ticket.contact:
        contact_name = ticket.contact.full_name or ""
        first_name = ticket.contact.first_name or ""
        contact_email = ticket.contact.email or ""
    else:
        contact_name = ""
        first_name = ""
        contact_email = ""

    content = content.replace("{{contact.name}}", contact_name)
    content = content.replace("{{contact.first_name}}", first_name)
    content = content.replace("{{contact.email}}", contact_email)

    # Agent variables
    content = content.replace(
        "{{agent.name}}", agent.get_full_name() or agent.email
    )
    content = content.replace(
        "{{agent.first_name}}", agent.first_name or agent.email.split("@")[0]
    )
    content = content.replace("{{agent.email}}", agent.email)

    # Increment usage count atomically
    response.usage_count = F("usage_count") + 1
    response.save(update_fields=["usage_count"])

    return content
