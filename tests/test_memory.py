import pytest

from plcmock.memory import AddressOutOfRange, MemorySpace


def test_word_and_bit_areas_support_cross_word_bit_access() -> None:
    memory = MemorySpace.from_config(
        {"words": {"D": {"size": 4}}, "bits": {"M": {"size": 64}}}
    )
    memory.word("D").write_words(1, [0x1234, 0xABCD])
    assert memory.word("D").read_words(1, 2) == [0x1234, 0xABCD]

    memory.word("D").write_bits(0, 15, [1, 1, 0])
    assert memory.word("D").read_bits(0, 15, 3) == [True, True, False]
    assert memory.word("D").read_words(0, 2) == [0x8000, 0x1235]

    memory.bit("M").write_packed_words(16, [0x8001, 0x00F0])
    assert memory.bit("M").read_packed_words(16, 2) == [0x8001, 0x00F0]


def test_bounds_are_fail_closed() -> None:
    memory = MemorySpace.from_config({"words": {"D": 2}, "bits": {"M": 8}})
    with pytest.raises(AddressOutOfRange):
        memory.word("D").read_words(1, 2)
    with pytest.raises(AddressOutOfRange):
        memory.bit("M").write_bits(7, [1, 0])
