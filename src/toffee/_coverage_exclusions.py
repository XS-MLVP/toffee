"""Static emptiness checks without enumerating integer ranges or mask domains."""
from __future__ import annotations


def _interval_cubes(low, high, width):
    """Represent a finite nonnegative interval as disjoint binary prefixes."""
    limit = (1 << width) - 1
    while low <= high:
        size = (low & -low) if low else 1 << width
        size = min(size, 1 << ((high - low + 1).bit_length() - 1))
        yield low, limit ^ (size - 1)
        low += size


def _uncovered(cube, exclusions):
    """Is any assignment in this cube outside the union of excluded cubes?"""
    pending = [cube]
    work = 0
    while pending:
        value, mask = pending.pop()
        overlaps = []
        for other_value, other_mask in exclusions:
            if (value ^ other_value) & mask & other_mask:
                continue
            if other_mask & ~mask == 0:
                break  # This exclusion contains the whole candidate cube.
            overlaps.append((other_value, other_mask))
        else:
            if not overlaps:
                return True
            bit = overlaps[0][1] & ~mask
            bit &= -bit
            pending.extend(((value & ~bit, mask | bit), (value | bit, mask | bit)))
        work += 1
        if work > 100_000:
            from .coverage import CoverageSchemaError
            raise CoverageSchemaError("bin exclusion analysis is too complex; simplify the masks")
    return False


def _integer_empty(matcher, exclusions):
    from .coverage import RangeMatcher, ValueMatcher, WildcardMatcher

    ranges = (matcher.ranges if isinstance(matcher, RangeMatcher) else ())
    finite = [bound for item in exclusions if isinstance(item, RangeMatcher)
              for pair in item.ranges for bound in pair]
    finite += [int(value) for item in exclusions if isinstance(item, ValueMatcher)
               for value in item.values if isinstance(value, int)]
    bounds = [bound for pair in ranges for bound in pair] + finite
    masks = [item.mask for item in (matcher, *exclusions) if isinstance(item, WildcardMatcher)]
    width = max([1, *(max(0, bound).bit_length() for bound in bounds),
                 *(mask.bit_length() for mask in masks)]) + 1
    excluded_ranges = [pair for item in exclusions if isinstance(item, RangeMatcher)
                       for pair in item.ranges]
    excluded_ranges += [(int(value), int(value)) for item in exclusions if isinstance(item, ValueMatcher)
                        for value in item.values if isinstance(value, int)]

    # Masks never match negative integers. Test negative intervals by unioning
    # finite intervals rather than constructing a two's-complement value space.
    for low, high in ranges:
        if low >= 0:
            continue
        end = min(high, -1)
        cursor = low
        for start, stop in sorted(excluded_ranges):
            if start > cursor or cursor > end:
                break
            if stop >= cursor:
                cursor = stop + 1
        if cursor <= end:
            return False

    excluded = [(item.value, item.mask) for item in exclusions if isinstance(item, WildcardMatcher)]
    for low, high in excluded_ranges:
        if high >= 0:
            excluded.extend(_interval_cubes(max(0, low), high, width))
    candidates = ([(matcher.value, matcher.mask)] if isinstance(matcher, WildcardMatcher) else
                  [cube for low, high in ranges if high >= 0
                   for cube in _interval_cubes(max(0, low), high, width)])
    return not any(_uncovered(cube, excluded) for cube in candidates)


def excluded_normal_bins(named):
    """Find bins that cannot receive a hit under the sampling priority rules."""
    from .coverage import BinKind, TransitionMatcher, ValueMatcher

    special = [item for item in named if item.spec.kind in (BinKind.IGNORE, BinKind.ILLEGAL)]
    static = [item.spec.matcher for item in special
              if not isinstance(item.spec.matcher, TransitionMatcher)]
    excluded = {}
    def statically_excluded(value):
        # Exact bins use Python equality: 0/1 and False/True are aliases,
        # whereas ranges and masks deliberately reject boolean samples.
        equivalents = [value]
        if isinstance(value, (bool, int)) and value in (0, 1):
            equivalents.extend((int(value), bool(value)))
        return all(any(other.matches(candidate) for other in static)
                   for candidate in equivalents)

    for item in named:
        if item.spec.kind is not BinKind.NORMAL:
            continue
        matcher = item.spec.matcher
        if isinstance(matcher, TransitionMatcher):
            # A static classification suppresses a transition's completion hit.
            empty = statically_excluded(matcher.values[-1])
            empty |= any(
                isinstance(other.spec.matcher, TransitionMatcher)
                and other.spec.matcher.values == matcher.values
                and (other.spec.matcher.overlap or not matcher.overlap)
                for other in special
            )
        elif isinstance(matcher, ValueMatcher):
            empty = all(statically_excluded(value) for value in matcher.values)
        else:
            empty = bool(static) and _integer_empty(matcher, static)
        if empty:
            excluded[item.name] = "all matching values or completions are excluded by ignore/illegal bins"
    return excluded
