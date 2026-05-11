"""Tests for MessageCounter."""

import pytest
import threading

from core.message_counter import MessageCounter


class TestMessageCounterInitial:
    def test_initial_count_is_zero(self):
        counter = MessageCounter()
        assert counter.count == 0


class TestMessageCounterIncrement:
    def test_increment_adds_two_by_default(self):
        counter = MessageCounter()
        counter.increment()
        assert counter.count == 2

    def test_increment_n_adds_exactly_n(self):
        counter = MessageCounter()
        counter.increment(5)
        assert counter.count == 5

    def test_increment_negative_raises_value_error(self):
        counter = MessageCounter()
        with pytest.raises(ValueError):
            counter.increment(-1)


class TestMessageCounterThreadSafety:
    def test_concurrent_increments(self):
        counter = MessageCounter()
        num_threads = 10
        increments_per_thread = 100

        def worker():
            for _ in range(increments_per_thread):
                counter.increment(1)

        threads = [threading.Thread(target=worker) for _ in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert counter.count == num_threads * increments_per_thread
