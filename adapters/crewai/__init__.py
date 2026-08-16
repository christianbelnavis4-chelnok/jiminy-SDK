from adapters.crewai.adapter import from_crew_output

__all__ = ["from_crew_output"]

# create_jiminy_event_listener is deliberately NOT imported here: it
# requires crewai.events (crewai>=0.86 or so), while from_crew_output
# above works from plain dicts with no crewai install at all. Importing
# adapters.crewai must stay dependency-free; import
# adapters.crewai.live directly to opt into the live event listener.
