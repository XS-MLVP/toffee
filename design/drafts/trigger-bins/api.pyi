"""Re-export real experimental signatures; no separate shadow API."""
from xreactor import TriggerDefinition as TriggerDefinition, xtrigger as xtrigger
from xreactor.declarative import (
    Bin as Bin, PatternBin as PatternBin, SignalCoverGroup as SignalCoverGroup,
    TemporalCoverPoint as TemporalCoverPoint, covergroup as covergroup,
)
