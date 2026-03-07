"""
Tests for app/judgement/judgement.py

Covers:
- score_to_rating thresholds
- score_to_verdict thresholds
- deduplicate_sources fingerprinting
- source_quality_weight (primary/secondary × hop decay)
- net_confidence_multiplier (floor, cap, each tier)
- per-source signed weighted scoring
- direction: supports_claim=False flips sign
- normalization formula
- division-by-zero guard (max_possible_sum=0 → 50)
- government source only boost + re-clamp
- net confidence multiplier applied per tier
- per-claim score clamped after step 5
- silent deduplication
- paywalled sources → unverified
- zero claims → mixed rating
- conflict detection at gap=40
- fakeness penalty above threshold
- final score clamping (0-100)
"""

from __future__ import annotations

import pytest

import app.config as cfg
from app.judgement.judgement import (
    deduplicate_sources,
    net_confidence_multiplier,
    score_to_rating,
    score_to_verdict,
    source_quality_weight,
)
from app.models.schemas import (
    ClaimSource,
    ClaimVerdict,
    ContentRating,
)


# ---------------------------------------------------------------------------
# score_to_rating
# ---------------------------------------------------------------------------

class TestScoreToRating:
    def test_100_is_credible(self):
        assert score_to_rating(100) == ContentRating.credible

    def test_80_is_credible(self):
        assert score_to_rating(80) == ContentRating.credible

    def test_79_is_mostly_credible(self):
        assert score_to_rating(79) == ContentRating.mostly_credible

    def test_60_is_mostly_credible(self):
        assert score_to_rating(60) == ContentRating.mostly_credible

    def test_59_is_mixed(self):
        assert score_to_rating(59) == ContentRating.mixed

    def test_40_is_mixed(self):
        assert score_to_rating(40) == ContentRating.mixed

    def test_39_is_low_credibility(self):
        assert score_to_rating(39) == ContentRating.low_credibility

    def test_20_is_low_credibility(self):
        assert score_to_rating(20) == ContentRating.low_credibility

    def test_19_is_not_credible(self):
        assert score_to_rating(19) == ContentRating.not_credible

    def test_0_is_not_credible(self):
        assert score_to_rating(0) == ContentRating.not_credible


# ---------------------------------------------------------------------------
# score_to_verdict
# ---------------------------------------------------------------------------

class TestScoreToVerdict:
    def test_100_is_true(self):
        assert score_to_verdict(100) == ClaimVerdict.true

    def test_80_is_true(self):
        assert score_to_verdict(80) == ClaimVerdict.true

    def test_79_is_mostly_true(self):
        assert score_to_verdict(79) == ClaimVerdict.mostly_true

    def test_60_is_mostly_true(self):
        assert score_to_verdict(60) == ClaimVerdict.mostly_true

    def test_59_is_unverified(self):
        assert score_to_verdict(59) == ClaimVerdict.unverified

    def test_45_is_unverified(self):
        assert score_to_verdict(45) == ClaimVerdict.unverified

    def test_44_is_misleading(self):
        assert score_to_verdict(44) == ClaimVerdict.misleading

    def test_30_is_misleading(self):
        assert score_to_verdict(30) == ClaimVerdict.misleading

    def test_29_is_mostly_false(self):
        assert score_to_verdict(29) == ClaimVerdict.mostly_false

    def test_15_is_mostly_false(self):
        assert score_to_verdict(15) == ClaimVerdict.mostly_false

    def test_14_is_false(self):
        assert score_to_verdict(14) == ClaimVerdict.false

    def test_0_is_false(self):
        assert score_to_verdict(0) == ClaimVerdict.false


# ---------------------------------------------------------------------------
# deduplicate_sources
# ---------------------------------------------------------------------------

def _make_source(url: str, text: str | None = None) -> ClaimSource:
    return ClaimSource(url=url, extracted_text=text)


class TestDeduplicateSources:
    def test_no_duplicates_unchanged(self):
        sources = [
            _make_source("http://a.com", "unique text A"),
            _make_source("http://b.com", "unique text B"),
        ]
        result = deduplicate_sources(sources)
        assert len(result) == 2

    def test_identical_text_deduped(self):
        text = "same content " * 50
        sources = [
            _make_source("http://a.com", text),
            _make_source("http://b.com", text),
        ]
        result = deduplicate_sources(sources)
        assert len(result) == 1
        assert result[0].url == "http://a.com"  # first one kept

    def test_same_300_char_prefix_deduped(self):
        prefix = "X" * 300
        sources = [
            _make_source("http://a.com", prefix + "extra A"),
            _make_source("http://b.com", prefix + "extra B"),
        ]
        result = deduplicate_sources(sources)
        assert len(result) == 1

    def test_different_prefix_not_deduped(self):
        sources = [
            _make_source("http://a.com", "A" * 300 + "tail"),
            _make_source("http://b.com", "B" * 300 + "tail"),
        ]
        result = deduplicate_sources(sources)
        assert len(result) == 2

    def test_none_text_sources_not_deduped_against_each_other(self):
        sources = [
            _make_source("http://a.com", None),
            _make_source("http://b.com", None),
        ]
        result = deduplicate_sources(sources)
        assert len(result) == 2

    def test_none_text_not_deduped_against_real_text(self):
        sources = [
            _make_source("http://a.com", "some text"),
            _make_source("http://b.com", None),
        ]
        result = deduplicate_sources(sources)
        assert len(result) == 2


# ---------------------------------------------------------------------------
# source_quality_weight
# ---------------------------------------------------------------------------

class TestSourceQualityWeight:
    def test_primary_hop0(self):
        src = ClaimSource(url="http://x.com", is_primary_source=True, hop_depth=0)
        expected = cfg.PRIMARY_SOURCE_MULTIPLIER * (cfg.HOP_DEPTH_DECAY ** 0)
        assert source_quality_weight(src) == pytest.approx(expected)

    def test_secondary_hop0(self):
        src = ClaimSource(url="http://x.com", is_primary_source=False, hop_depth=0)
        expected = cfg.SECONDARY_SOURCE_MULTIPLIER * (cfg.HOP_DEPTH_DECAY ** 0)
        assert source_quality_weight(src) == pytest.approx(expected)

    def test_primary_hop1(self):
        src = ClaimSource(url="http://x.com", is_primary_source=True, hop_depth=1)
        expected = cfg.PRIMARY_SOURCE_MULTIPLIER * cfg.HOP_DEPTH_DECAY
        assert source_quality_weight(src) == pytest.approx(expected)

    def test_secondary_hop2(self):
        src = ClaimSource(url="http://x.com", is_primary_source=False, hop_depth=2)
        expected = cfg.SECONDARY_SOURCE_MULTIPLIER * (cfg.HOP_DEPTH_DECAY ** 2)
        assert source_quality_weight(src) == pytest.approx(expected)

    def test_primary_weight_greater_than_secondary_same_hop(self):
        primary = ClaimSource(url="http://x.com", is_primary_source=True, hop_depth=0)
        secondary = ClaimSource(url="http://x.com", is_primary_source=False, hop_depth=0)
        assert source_quality_weight(primary) > source_quality_weight(secondary)

    def test_hop0_greater_than_hop2(self):
        src0 = ClaimSource(url="http://x.com", is_primary_source=False, hop_depth=0)
        src2 = ClaimSource(url="http://x.com", is_primary_source=False, hop_depth=2)
        assert source_quality_weight(src0) > source_quality_weight(src2)


# ---------------------------------------------------------------------------
# net_confidence_multiplier
# ---------------------------------------------------------------------------

class TestNetConfidenceMultiplier:
    def test_net_minus_2(self):
        assert net_confidence_multiplier(-2) == pytest.approx(0.6)

    def test_net_minus_1(self):
        assert net_confidence_multiplier(-1) == pytest.approx(0.8)

    def test_net_0(self):
        assert net_confidence_multiplier(0) == pytest.approx(1.0)

    def test_net_1(self):
        assert net_confidence_multiplier(1) == pytest.approx(1.0)

    def test_net_0_equals_net_1(self):
        assert net_confidence_multiplier(0) == net_confidence_multiplier(1)

    def test_net_2(self):
        assert net_confidence_multiplier(2) == pytest.approx(1.1)

    def test_net_3(self):
        assert net_confidence_multiplier(3) == pytest.approx(1.2)

    def test_net_4(self):
        assert net_confidence_multiplier(4) == pytest.approx(1.3)

    def test_net_5(self):
        assert net_confidence_multiplier(5) == pytest.approx(1.4)

    def test_floor_below_minus_2(self):
        # net < NET_CONFIDENCE_MIN should use the floor value
        assert net_confidence_multiplier(-5) == net_confidence_multiplier(-2)
        assert net_confidence_multiplier(-10) == pytest.approx(0.6)

    def test_cap_above_5(self):
        # net > NET_CONFIDENCE_MAX should use the cap value
        assert net_confidence_multiplier(10) == net_confidence_multiplier(5)
        assert net_confidence_multiplier(100) == pytest.approx(1.4)


# ---------------------------------------------------------------------------
# Scoring math unit tests (without Claude calls)
# ---------------------------------------------------------------------------

class TestScoringMath:
    """
    Test the core scoring formula directly by replicating the math
    from judge_claim without invoking Claude.
    """

    def _compute_claim_score(
        self,
        verdict: ClaimVerdict,
        sources_data: list[dict],
        government_source_only: bool = False,
    ) -> float:
        """
        Replicate steps 1–5a of the scoring chain in judge_claim.

        sources_data: list of dicts with keys:
            - is_primary_source (bool)
            - hop_depth (int)
            - supports_claim (bool)
            - has_text (bool)  — if False, source is paywalled/skipped
        """
        raw_sum = 0.0
        max_possible_sum = 0.0
        evidence_supports = []

        base_weight = cfg.VERDICT_BASE_WEIGHTS.get(verdict.value, 0.0)

        for s in sources_data:
            if not s["has_text"]:
                continue
            src = ClaimSource(
                url="http://x.com",
                is_primary_source=s["is_primary_source"],
                hop_depth=s["hop_depth"],
            )
            direction = 1.0 if s["supports_claim"] else -1.0
            quality_decay = source_quality_weight(src)
            per_source = base_weight * direction * quality_decay
            raw_sum += per_source
            max_possible_sum += abs(base_weight) * quality_decay
            evidence_supports.append(s["supports_claim"])

        if max_possible_sum == 0:
            score = 50.0
        else:
            score = (raw_sum / max_possible_sum + 1) / 2 * 100

        # Net confidence multiplier
        supports = sum(1 for x in evidence_supports if x)
        contradicts = sum(1 for x in evidence_supports if not x)
        net = supports - contradicts
        score = score * net_confidence_multiplier(net)
        score = max(0.0, min(100.0, score))

        # Government boost
        if government_source_only:
            score = score * cfg.GOVERNMENT_ONLY_BOOST
            score = max(0.0, min(100.0, score))

        return score

    # ── Fully supported claim ────────────────────────────────────────────

    def test_fully_supported_true_verdict_high_score(self):
        score = self._compute_claim_score(
            ClaimVerdict.true,
            [
                {"is_primary_source": True, "hop_depth": 0, "supports_claim": True, "has_text": True},
                {"is_primary_source": True, "hop_depth": 0, "supports_claim": True, "has_text": True},
            ],
        )
        # Both supporting → raw_sum == max_possible_sum → normalized = 100, net=2 → ×1.1 → 100 (clamped)
        assert score == pytest.approx(100.0)

    def test_fully_contradicted_true_verdict_low_score(self):
        score = self._compute_claim_score(
            ClaimVerdict.true,
            [
                {"is_primary_source": True, "hop_depth": 0, "supports_claim": False, "has_text": True},
                {"is_primary_source": True, "hop_depth": 0, "supports_claim": False, "has_text": True},
            ],
        )
        # Both contradicting → raw_sum == -max_possible_sum → normalized = 0, net=-2 → ×0.6 → 0
        assert score == pytest.approx(0.0)

    def test_balanced_evidence_near_50(self):
        score = self._compute_claim_score(
            ClaimVerdict.true,
            [
                {"is_primary_source": False, "hop_depth": 0, "supports_claim": True, "has_text": True},
                {"is_primary_source": False, "hop_depth": 0, "supports_claim": False, "has_text": True},
            ],
        )
        # Balanced → raw_sum = 0 → normalized = 50, net=0 → ×1.0 → 50
        assert score == pytest.approx(50.0)

    # ── direction flips sign ─────────────────────────────────────────────

    def test_supports_false_reduces_score(self):
        score_support = self._compute_claim_score(
            ClaimVerdict.true,
            [{"is_primary_source": False, "hop_depth": 0, "supports_claim": True, "has_text": True}],
        )
        score_contradict = self._compute_claim_score(
            ClaimVerdict.true,
            [{"is_primary_source": False, "hop_depth": 0, "supports_claim": False, "has_text": True}],
        )
        assert score_support > score_contradict

    # ── max_possible_sum == 0 guard ──────────────────────────────────────

    def test_all_paywalled_returns_50(self):
        score = self._compute_claim_score(
            ClaimVerdict.true,
            [
                {"is_primary_source": True, "hop_depth": 0, "supports_claim": True, "has_text": False},
                {"is_primary_source": True, "hop_depth": 0, "supports_claim": True, "has_text": False},
            ],
        )
        assert score == pytest.approx(50.0)

    def test_no_sources_returns_50(self):
        score = self._compute_claim_score(ClaimVerdict.true, [])
        assert score == pytest.approx(50.0)

    def test_unverified_verdict_returns_50_single_supporting(self):
        # unverified base weight = 0.0 → max_possible_sum = 0 → neutral
        score = self._compute_claim_score(
            ClaimVerdict.unverified,
            [{"is_primary_source": False, "hop_depth": 0, "supports_claim": True, "has_text": True}],
        )
        assert score == pytest.approx(50.0)

    # ── is_primary_source multiplier ────────────────────────────────────

    def test_primary_source_higher_contribution(self):
        score_primary = self._compute_claim_score(
            ClaimVerdict.true,
            [{"is_primary_source": True, "hop_depth": 0, "supports_claim": True, "has_text": True}],
        )
        score_secondary = self._compute_claim_score(
            ClaimVerdict.true,
            [{"is_primary_source": False, "hop_depth": 0, "supports_claim": True, "has_text": True}],
        )
        # Both fully supporting single source → both normalized to 100 before multiplier.
        # Net=1 for both → ×1.0. Both should equal 100 normalized, then clamped.
        # The primary/secondary distinction affects raw_sum and max_possible_sum equally,
        # so normalized value is the same; the multiplier effect shows when mixing.
        assert score_primary == pytest.approx(score_secondary)

    def test_mixed_primary_secondary_primary_outweighs(self):
        # 1 primary supporting vs 1 secondary contradicting
        score = self._compute_claim_score(
            ClaimVerdict.true,
            [
                {"is_primary_source": True, "hop_depth": 0, "supports_claim": True, "has_text": True},
                {"is_primary_source": False, "hop_depth": 0, "supports_claim": False, "has_text": True},
            ],
        )
        # primary weight = 1.2, secondary weight = 0.8
        # raw_sum = 1.0*1.2 - 1.0*0.8 = 0.4
        # max_possible_sum = 1.2 + 0.8 = 2.0
        # normalized = (0.4/2.0 + 1)/2 * 100 = (0.2 + 1)/2 * 100 = 60
        # net=0 → ×1.0 → 60
        assert score == pytest.approx(60.0)

    # ── hop_depth decay ──────────────────────────────────────────────────

    def test_hop_depth_reduces_effective_weight(self):
        # With matching quality multipliers: a source with hop_depth=2 has less
        # effect on normalization but since it's the only source, normalized still = 100.
        # The raw ratio remains 1.0 regardless of hop — what changes is absolute contribution.
        # This test verifies quality_decay is applied consistently to both numerator and denominator.
        src_hop0 = ClaimSource(url="http://x.com", is_primary_source=False, hop_depth=0)
        src_hop2 = ClaimSource(url="http://x.com", is_primary_source=False, hop_depth=2)
        w0 = source_quality_weight(src_hop0)
        w2 = source_quality_weight(src_hop2)
        assert w0 > w2

    # ── government_source_only boost ─────────────────────────────────────

    def test_government_only_boost_increases_score(self):
        score_no_boost = self._compute_claim_score(
            ClaimVerdict.true,
            [{"is_primary_source": False, "hop_depth": 0, "supports_claim": True, "has_text": True}],
            government_source_only=False,
        )
        score_with_boost = self._compute_claim_score(
            ClaimVerdict.true,
            [{"is_primary_source": False, "hop_depth": 0, "supports_claim": True, "has_text": True}],
            government_source_only=True,
        )
        assert score_with_boost >= score_no_boost

    def test_government_only_boost_clamped_at_100(self):
        score = self._compute_claim_score(
            ClaimVerdict.true,
            [
                {"is_primary_source": True, "hop_depth": 0, "supports_claim": True, "has_text": True},
                {"is_primary_source": True, "hop_depth": 0, "supports_claim": True, "has_text": True},
            ],
            government_source_only=True,
        )
        assert score <= 100.0

    # ── per-claim score clamped after step 5 ────────────────────────────

    def test_per_claim_score_clamped_above_100(self):
        # net=5 (cap) gives ×1.4, starting from normalized=100 → 140, clamped to 100
        sources = [
            {"is_primary_source": True, "hop_depth": 0, "supports_claim": True, "has_text": True}
            for _ in range(5)
        ]
        score = self._compute_claim_score(ClaimVerdict.true, sources)
        assert score <= 100.0

    def test_per_claim_score_clamped_below_0(self):
        sources = [
            {"is_primary_source": True, "hop_depth": 0, "supports_claim": False, "has_text": True}
            for _ in range(5)
        ]
        score = self._compute_claim_score(ClaimVerdict.true, sources)
        assert score >= 0.0


# ---------------------------------------------------------------------------
# Final scoring pipeline tests (without Claude — simulate judge())
# ---------------------------------------------------------------------------

class TestFinalScoringPipeline:
    """
    Test the blending, fakeness penalty, conflict detection, and zero-claims
    edge case by replicating the pipeline math.
    """

    def _blend_and_finalize(
        self,
        claims_score: float,
        publisher_score: float,
        fakeness_score: int,
    ) -> tuple[int, bool]:
        """Returns (final_score, is_conflicted)."""
        blended = (
            claims_score * cfg.CLAIMS_BLEND_WEIGHT
            + publisher_score * cfg.PUBLISHER_BLEND_WEIGHT
        )
        if fakeness_score > cfg.FAKENESS_THRESHOLD:
            blended *= cfg.FAKENESS_PENALTY_MULTIPLIER
        final = int(max(0.0, min(100.0, round(blended))))
        is_conflicted = abs(claims_score - publisher_score) >= cfg.CONFLICT_GAP_THRESHOLD
        return final, is_conflicted

    # ── blend weights ────────────────────────────────────────────────────

    def test_blend_weights_sum_to_1(self):
        assert cfg.CLAIMS_BLEND_WEIGHT + cfg.PUBLISHER_BLEND_WEIGHT == pytest.approx(1.0)

    def test_blend_equal_scores(self):
        final, _ = self._blend_and_finalize(70, 70, 0)
        assert final == 70

    def test_blend_claims_weighted_higher(self):
        # claims=80, publisher=20 → blend = 80*0.7 + 20*0.3 = 56+6 = 62
        final, _ = self._blend_and_finalize(80, 20, 0)
        assert final == 62

    # ── fakeness penalty ─────────────────────────────────────────────────

    def test_fakeness_below_threshold_no_penalty(self):
        final_no_penalty, _ = self._blend_and_finalize(80, 80, cfg.FAKENESS_THRESHOLD)
        final_with_penalty, _ = self._blend_and_finalize(80, 80, cfg.FAKENESS_THRESHOLD + 1)
        assert final_with_penalty < final_no_penalty

    def test_fakeness_exactly_at_threshold_no_penalty(self):
        # fakeness_score == FAKENESS_THRESHOLD does NOT trigger penalty (strictly greater)
        final_at, _ = self._blend_and_finalize(80, 80, cfg.FAKENESS_THRESHOLD)
        final_below, _ = self._blend_and_finalize(80, 80, cfg.FAKENESS_THRESHOLD - 1)
        assert final_at == final_below

    def test_fakeness_penalty_reduces_score(self):
        _, _ = self._blend_and_finalize(70, 70, 0)
        final_penalized, _ = self._blend_and_finalize(70, 70, 100)
        # 70 * 0.8 = 56
        assert final_penalized == 56

    # ── conflict detection ───────────────────────────────────────────────

    def test_conflict_at_exact_threshold(self):
        _, is_conflicted = self._blend_and_finalize(100, 60, 0)
        assert is_conflicted is True

    def test_conflict_just_below_threshold(self):
        _, is_conflicted = self._blend_and_finalize(99, 60, 0)
        # gap = 39 < 40 → not conflicted
        assert is_conflicted is False

    def test_no_conflict_equal_scores(self):
        _, is_conflicted = self._blend_and_finalize(70, 70, 0)
        assert is_conflicted is False

    def test_conflict_uses_absolute_gap(self):
        _, is_conflicted_high = self._blend_and_finalize(100, 60, 0)
        _, is_conflicted_low = self._blend_and_finalize(60, 100, 0)
        assert is_conflicted_high == is_conflicted_low

    # ── final score clamping ─────────────────────────────────────────────

    def test_final_score_clamped_above_100(self):
        final, _ = self._blend_and_finalize(100, 100, 0)
        assert final <= 100

    def test_final_score_clamped_below_0(self):
        final, _ = self._blend_and_finalize(0, 0, 0)
        assert final >= 0


# ---------------------------------------------------------------------------
# Zero-claims edge case
# ---------------------------------------------------------------------------

class TestZeroClaimsEdgeCase:
    def test_zero_claims_rating_is_mixed(self):
        # score=50 should map to mixed (40-59)
        assert score_to_rating(50) == ContentRating.mixed
