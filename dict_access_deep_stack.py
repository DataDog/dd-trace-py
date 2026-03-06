#!/usr/bin/env python
"""
Deep stack exception benchmark:
Throw an exception at the bottom of a 20-frame call stack,
catch it at the top with some fake handling logic.
"""



def frame_10():
    raise KeyError("target_key")


def frame_09():
    try:
        frame_10()
    except KeyError as e:
        pass


def frame_08():
    return frame_09()


def frame_07():
    return frame_08()


def frame_06():
    return frame_07()


def frame_05():
    return frame_06()


def frame_04():
    return frame_05()


def frame_03():
    return frame_04()


def frame_02():
    return frame_03()


def frame_01():
    return frame_02()


def handle_error_7(e):
    print("handled error")

def handle_error_6(e):
    handle_error_7(e)

def handle_error_5(e):
    handle_error_6(e)

def handle_error_4(e):
    handle_error_5(e)

def handle_error_3(e):
    handle_error_4(e)


def handle_error_2(e):
    handle_error_3(e)


def handle_error_1(e):
    handle_error_2(e)


def main():
    while True:
        try:
            frame_01()
        except KeyError as e:
            handle_error_1(e)


if __name__ == "__main__":
    main()
