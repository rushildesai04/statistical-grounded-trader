from sgt.ops.sms_style import bold, italic


def test_bold_and_italic_map_letters_digits_and_pass_everything_else():
    assert bold("Ab9 $+,.%") == "𝗔𝗯𝟵 $+,.%"
    assert italic("Ab9 (x)") == "𝘈𝘣9 (𝘹)"                 # Unicode has no italic digits
    assert not bold("OK 42").isascii() and bold("OK 42") != "OK 42"
