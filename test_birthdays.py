from datetime import date

from birthdays import Birthday, is_birthday_today, parse_birthday


def test_parse_birthday():
    b = parse_birthday("15.03")
    assert b == Birthday(month=3, day=15)
    assert parse_birthday("29/02/2000") == Birthday(month=2, day=29, year=2000)
    assert parse_birthday("32.01") is None
    assert parse_birthday("hello") is None


def test_leap_day_fallback():
    b = Birthday(month=2, day=29)
    assert is_birthday_today(b, date(2024, 2, 29))
    assert is_birthday_today(b, date(2023, 2, 28))
    assert not is_birthday_today(b, date(2023, 2, 27))


if __name__ == "__main__":
    test_parse_birthday()
    test_leap_day_fallback()
    print("ok")
