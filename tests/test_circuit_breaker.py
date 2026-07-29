from __future__ import annotations

import time

from circuit_breaker import CircuitBreaker, CircuitState


class TestCircuitBreaker:
    def test_initial_state_is_closed(self) -> None:
        cb = CircuitBreaker(name="test")
        assert cb.state == CircuitState.CLOSED
        assert not cb.is_open

    def test_before_request_allows_when_closed(self) -> None:
        cb = CircuitBreaker(name="test")
        assert cb.before_request() is True

    def test_on_success_resets_failure_count(self) -> None:
        cb = CircuitBreaker(name="test", failure_threshold=3)
        cb.on_failure()
        cb.on_failure()
        cb.on_success()
        assert cb.failure_count == 0
        assert cb.state == CircuitState.CLOSED

    def test_opens_after_threshold_failures(self) -> None:
        cb = CircuitBreaker(name="test", failure_threshold=3)
        cb.on_failure()
        cb.on_failure()
        cb.on_failure()
        assert cb.state == CircuitState.OPEN
        assert cb.is_open

    def test_blocks_request_when_open(self) -> None:
        cb = CircuitBreaker(name="test", failure_threshold=2, recovery_timeout=60.0)
        cb.on_failure()
        cb.on_failure()
        assert cb.is_open
        assert cb.before_request() is False

    def test_transitions_to_half_open_after_timeout(self) -> None:
        cb = CircuitBreaker(name="test", failure_threshold=2, recovery_timeout=0.01)
        cb.on_failure()
        cb.on_failure()
        assert cb.is_open
        time.sleep(0.02)
        assert cb.before_request() is True
        assert cb.state == CircuitState.HALF_OPEN

    def test_half_open_to_closed_on_success(self) -> None:
        cb = CircuitBreaker(name="test", failure_threshold=2, recovery_timeout=0.01)
        cb.on_failure()
        cb.on_failure()
        time.sleep(0.02)
        cb.before_request()
        cb.on_success()
        assert cb.state == CircuitState.CLOSED

    def test_half_open_back_to_open_on_failure(self) -> None:
        cb = CircuitBreaker(name="test", failure_threshold=2, recovery_timeout=0.01)
        cb.on_failure()
        cb.on_failure()
        time.sleep(0.02)
        cb.before_request()
        cb.on_failure()
        assert cb.state == CircuitState.OPEN

    def test_half_open_limits_to_one_probe(self) -> None:
        cb = CircuitBreaker(name="test", failure_threshold=2, recovery_timeout=0.01)
        cb.on_failure()
        cb.on_failure()
        time.sleep(0.02)
        assert cb.before_request() is True
        assert cb.before_request() is False

    def test_reset_clears_all_state(self) -> None:
        cb = CircuitBreaker(name="test", failure_threshold=2)
        cb.on_failure()
        cb.on_failure()
        assert cb.is_open
        cb.reset()
        assert cb.state == CircuitState.CLOSED
        assert cb.failure_count == 0
        assert cb.is_open is False

    def test_failures_below_threshold_keep_closed(self) -> None:
        cb = CircuitBreaker(name="test", failure_threshold=5)
        for _ in range(4):
            cb.on_failure()
        assert cb.state == CircuitState.CLOSED

    def test_success_after_partial_failures_resets(self) -> None:
        cb = CircuitBreaker(name="test", failure_threshold=5)
        for _ in range(4):
            cb.on_failure()
        cb.on_success()
        assert cb.failure_count == 0

    def test_status_contains_expected_fields(self) -> None:
        cb = CircuitBreaker(name="balance", failure_threshold=3)
        status = cb.status()
        assert status["name"] == "balance"
        assert status["state"] == "closed"
        assert status["failure_count"] == 0
        assert status["opened_at"] is None
