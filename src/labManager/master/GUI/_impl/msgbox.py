import typing
from imgui_bundle import hello_imgui, imgui, icons_fontawesome
from enum import Enum, auto

from . import utils

icon_font = None

class MsgBox(Enum):
    question= auto()
    info    = auto()
    warn    = auto()
    error   = auto()

def msgbox(title: str, msg: str, type: MsgBox = None, buttons: dict[str, typing.Callable] = True, more: str = None):
    def popup_content():
        spacing = 2 * imgui.get_style().item_spacing.x
        if type is MsgBox.question:
            icon = icons_fontawesome.ICON_FA_QUESTION_CIRCLE
            color = (0.45, 0.09, 1.00)
        elif type is MsgBox.info:
            icon = icons_fontawesome.ICON_FA_INFO_CIRCLE
            color = (0.10, 0.69, 0.95)
        elif type is MsgBox.warn:
            icon = icons_fontawesome.ICON_FA_EXCLAMATION_TRIANGLE
            color = (0.95, 0.69, 0.10)
        elif type is MsgBox.error:
            icon = icons_fontawesome.ICON_FA_EXCLAMATION_TRIANGLE
            color = (0.95, 0.22, 0.22)
        else:
            icon = None
        if icon:
            imgui.push_font(icon_font)
            icon_size = imgui.calc_text_size(icon)
            imgui.text_colored((*color,1.),icon)
            imgui.pop_font()
            imgui.same_line(spacing=spacing)
        imgui.begin_group()
        msg_size_y = imgui.calc_text_size(msg).y
        if more:
            msg_size_y += imgui.get_text_line_height_with_spacing() + imgui.get_frame_height_with_spacing()
        if icon and (diff := icon_size.y - msg_size_y) > 0:
            imgui.dummy((0, diff / 2 - imgui.get_style().item_spacing.y))
        imgui.text_unformatted(msg)
        if more:
            imgui.text("")
            if imgui.tree_node_ex("More info", flags=imgui.TreeNodeFlags_.span_avail_width):
                size = imgui.get_io().display_size
                more_size = imgui.calc_text_size(more)
                _26 = hello_imgui.dpi_window_size_factor()*26 + imgui.get_style().scrollbar_size
                width = min(more_size.x + _26, size.x * 0.8 - icon_size.x)
                height = min(more_size.y + _26, size.y * 0.7 - msg_size_y)
                imgui.input_text_multiline(f"###more_info_{title}", more, (width, height), flags=imgui.InputTextFlags_.read_only)
                imgui.tree_pop()
        imgui.end_group()
        imgui.same_line(spacing=spacing)
        imgui.dummy((0, 0))
    return utils.popup(title, popup_content, buttons, closable=False, outside=False)


class Exc(Exception):
    def __init__(self, title:str, msg: str, type: MsgBox = None, buttons: dict[str, typing.Callable] = True, more: str = None):
        self.title = title
        self.msg = msg
        self.popup = utils.push_popup(msgbox, title, msg, type, buttons, more)
