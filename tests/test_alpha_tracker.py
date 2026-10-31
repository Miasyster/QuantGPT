"""Tests for alpha_tracker — similarity scoring and self-correlation check."""

import uuid

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from quantgpt.alpha_tracker import compute_similarity
from quantgpt.models import Base, SubmittedAlpha


@pytest_asyncio.fixture
async def alpha_engine():
    eng = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture
async def alpha_factory(alpha_engine):
    return async_sessionmaker(alpha_engine, class_=AsyncSession, expire_on_commit=False)


class TestComputeSimilarity:
    def test_identical_expressions(self):
        sim = compute_similarity("rank(close/open)", "rank(close/open)")
        assert sim["text_similarity"] == 1.0
        assert sim["overall_similarity"] == 1.0

    def test_completely_different(self):
        sim = compute_similarity("rank(close)", "ts_mean(volume, 20)")
        assert sim["overall_similarity"] < 0.5

    def test_similar_expressions(self):
        sim = compute_similarity(
            "rank(close/ts_mean(close, 20))",
            "rank(close/ts_mean(close, 10))",
        )
        assert sim["operator_overlap"] == 1.0
        assert sim["field_overlap"] == 1.0
        assert sim["overall_similarity"] > 0.8

    def test_whitespace_normalization(self):
        sim = compute_similarity("rank( close / open )", "rank(close/open)")
        assert sim["text_similarity"] == 1.0

    def test_case_insensitive(self):
        sim = compute_similarity("RANK(Close)", "rank(close)")
        assert sim["text_similarity"] == 1.0

    def test_different_operators_same_fields(self):
        sim = compute_similarity("rank(close)", "zscore(close)")
        assert sim["field_overlap"] == 1.0
        assert sim["operator_overlap"] == 0.0
        assert sim["overall_similarity"] < 0.8

    def test_same_operators_different_fields(self):
        sim = compute_similarity("rank(close)", "rank(volume)")
        assert sim["operator_overlap"] == 1.0
        assert sim["field_overlap"] == 0.0


class TestCheckSelfCorrelation:
    @pytest.mark.asyncio
    async def test_no_submitted_alphas(self, alpha_factory):
        """When no alphas are submitted, expression is safe."""
        from sqlalchemy import select

        user_id = uuid.uuid4()
        async with alpha_factory() as session:
            result = await session.execute(
                select(SubmittedAlpha).where(SubmittedAlpha.user_id == user_id)
            )
            existing = result.scalars().all()
        assert len(existing) == 0

    @pytest.mark.asyncio
    async def test_detects_exact_match(self, alpha_factory):
        user_id = uuid.uuid4()

        async with alpha_factory() as session:
            from quantgpt.expression_parser import normalize_expression
            session.add(SubmittedAlpha(
                user_id=user_id,
                alpha_id="abc123",
                expression="rank(close/open)",
                expression_normalized=normalize_expression("rank(close/open)"),
                region="USA",
                universe="TOP3000",
                delay=1,
                decay=0,
                neutralization="SUBINDUSTRY",
                truncation=0.08,
            ))
            await session.commit()

        from sqlalchemy import select

        async with alpha_factory() as session:
            result = await session.execute(
                select(SubmittedAlpha).where(SubmittedAlpha.user_id == user_id)
            )
            existing = result.scalars().all()

        assert len(existing) == 1
        sim = compute_similarity("rank(close/open)", existing[0].expression)
        assert sim["overall_similarity"] == 1.0

    @pytest.mark.asyncio
    async def test_passes_for_different_expression(self, alpha_factory):
        user_id = uuid.uuid4()

        async with alpha_factory() as session:
            from quantgpt.expression_parser import normalize_expression
            session.add(SubmittedAlpha(
                user_id=user_id,
                alpha_id="abc123",
                expression="rank(close/open)",
                expression_normalized=normalize_expression("rank(close/open)"),
                region="USA",
                universe="TOP3000",
                delay=1,
                decay=0,
                neutralization="SUBINDUSTRY",
                truncation=0.08,
            ))
            await session.commit()

        from sqlalchemy import select

        async with alpha_factory() as session:
            result = await session.execute(
                select(SubmittedAlpha).where(SubmittedAlpha.user_id == user_id)
            )
            existing = result.scalars().all()

        sim = compute_similarity("ts_mean(volume, 20) / ts_std(volume, 20)", existing[0].expression)
        assert sim["overall_similarity"] < 0.85


class TestSubmittedAlphaModel:
    @pytest.mark.asyncio
    async def test_create_and_read(self, alpha_factory):
        user_id = uuid.uuid4()
        async with alpha_factory() as session:
            alpha = SubmittedAlpha(
                user_id=user_id,
                alpha_id="test_alpha_001",
                expression="rank(close)",
                expression_normalized="rank(close)",
                region="USA",
                universe="TOP3000",
                delay=1,
                sharpe=1.5,
                fitness=1.2,
            )
            session.add(alpha)
            await session.commit()

        from sqlalchemy import select
        async with alpha_factory() as session:
            result = await session.execute(
                select(SubmittedAlpha).where(SubmittedAlpha.alpha_id == "test_alpha_001")
            )
            loaded = result.scalar_one()
            assert loaded.expression == "rank(close)"
            assert loaded.sharpe == 1.5
            assert loaded.fitness == 1.2
            assert loaded.region == "USA"
            assert loaded.status == "submitted"
