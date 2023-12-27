import random
import functools
import traceback
from imgui_bundle import imgui, icons_fontawesome
import sys
from typing import Any, Callable

from . import msgbox


# https://gist.github.com/Willy-JL/f733c960c6b0d2284bcbee0316f88878
def get_traceback(*exc_info: list):
    exc_info = exc_info or sys.exc_info()
    tb_lines = traceback.format_exception(*exc_info)
    tb = "".join(tb_lines)
    return tb


def push_disabled(block_interaction=True):
    if block_interaction:
        imgui.internal.push_item_flag(imgui.internal.ItemFlags_.disabled, True)
    imgui.push_style_var(imgui.StyleVar_.alpha, imgui.get_style().alpha *  0.5)

def pop_disabled(block_interaction=True):
    if block_interaction:
        imgui.internal.pop_item_flag()
    imgui.pop_style_var()


def trim_str(text: str, length=None, till_newline=True, newline_ellipsis=False):
    if text and till_newline:
        temp = text.splitlines()
        if temp:
            text = temp[0]
        if len(temp)>1 and newline_ellipsis:
            text += '..'
    if length:
        text = (text[:length-2] + '..') if len(text) > length else text
    return text


def set_all(input: dict[int, bool], value, subset: list[int] = None, predicate: Callable = None):
    if subset is None:
        subset = (r for r in input)
    for r in subset:
        if not predicate or predicate(r):
            input[r] = value

def selectable_item_logic(id: int, selected: dict[int,Any], last_clicked_id: int, sorted_ids: list[int],
                          selectable_clicked: bool, new_selectable_state: bool,
                          allow_multiple=True, overlayed_hovered=False, overlayed_clicked=False, new_overlayed_state=False):
    if overlayed_clicked:
        if not allow_multiple:
            set_all(selected, False)
        selected[id] = new_overlayed_state
        last_clicked_id = id
    elif selectable_clicked and not overlayed_hovered: # don't enter this branch if interaction is with another overlaid actionable item
        if not allow_multiple:
            set_all(selected, False)
            selected[id] = new_selectable_state
        else:
            num_selected = sum([selected[id] for id in sorted_ids])
            if not imgui.get_io().key_ctrl:
                # deselect all, below we'll either select all, or range between last and current clicked
                set_all(selected, False)

            if imgui.get_io().key_shift:
                # select range between last clicked and just clicked item
                idx              = sorted_ids.index(id)
                last_clicked_idx = sorted_ids.index(last_clicked_id)
                idxs = sorted([idx, last_clicked_idx])
                for rid in range(idxs[0],idxs[1]+1):
                    selected[sorted_ids[rid]] = True
            else:
                selected[id] = True if num_selected>1 and not imgui.get_io().key_ctrl else new_selectable_state

            # consistent with Windows behavior, only update last clicked when shift not pressed
            if not imgui.get_io().key_shift:
                last_clicked_id = id

    return last_clicked_id


def draw_tooltip(hover_text):
    imgui.begin_tooltip()
    imgui.push_text_wrap_pos(min(imgui.get_font_size() * 35, imgui.get_io().display_size.x))
    imgui.text_unformatted(hover_text)
    imgui.pop_text_wrap_pos()
    imgui.end_tooltip()

def draw_hover_text(hover_text: str, text="(?)", force=False, hovered_flags=0, *args, **kwargs):
    if text:
        imgui.text_disabled(text, *args, **kwargs)
    if force or imgui.is_item_hovered(hovered_flags):
        draw_tooltip(hover_text)
        return True
    return False


def fix_popup_transparency():
    frame_bg_col = list(imgui.get_style().color_(imgui.Col_.title_bg_active))
    imgui.get_style().set_color_(imgui.Col_.title_bg_active,(*frame_bg_col[0:3], 1.))
    popup_bg_col = list(imgui.get_style().color_(imgui.Col_.popup_bg))
    imgui.get_style().set_color_(imgui.Col_.popup_bg,(*popup_bg_col[0:3], 1.))

def close_weak_popup(check_escape: bool = True, check_click_outside: bool = True):
    if not imgui.is_popup_open("", imgui.PopupFlags_.any_popup_id):
        # This is the topmost popup
        if check_escape and imgui.is_key_pressed(imgui.Key.escape):
            # Escape is pressed
            imgui.close_current_popup()
            return True
        elif check_click_outside and imgui.is_mouse_clicked(imgui.MouseButton_.left):
            # Mouse was just clicked
            pos = imgui.get_window_pos()
            size = imgui.get_window_size()
            if not imgui.is_mouse_hovering_rect(pos, (pos.x+size.x, pos.y+size.y), clip=False):
                # Popup is not hovered
                imgui.close_current_popup()
                return True
    return False

popup_flags: int = (
    imgui.WindowFlags_.no_collapse |
    imgui.WindowFlags_.no_saved_settings |
    imgui.WindowFlags_.always_auto_resize
)

def popup(label: str, popup_content: Callable, buttons: dict[str, Callable] = None, closable=True, escape=True, outside=True):
    if buttons is True:
        buttons = {
            icons_fontawesome.ICON_FA_CHECK + " Ok": None
        }
    if not imgui.is_popup_open(label):
        imgui.open_popup(label)
    closed = False
    opened = 1
    if imgui.begin_popup_modal(label, closable or None, flags=popup_flags)[0]:
        if escape or outside:
            closed = close_weak_popup(check_escape=escape, check_click_outside=outside)
        imgui.begin_group()
        activate_button = popup_content()
        imgui.end_group()
        imgui.spacing()
        if buttons:
            btns_width = sum(imgui.calc_text_size(name).x for name in buttons) + (2 * len(buttons) * imgui.get_style().frame_padding.x) + (imgui.get_style().item_spacing.x * (len(buttons) - 1))
            cur_pos_x = imgui.get_cursor_pos_x()
            new_pos_x = cur_pos_x + imgui.get_content_region_avail().x - btns_width
            if new_pos_x > cur_pos_x:
                imgui.set_cursor_pos_x(new_pos_x)
            for i, (label,callback) in enumerate(buttons.items()):
                if imgui.button(label) or activate_button==i:
                    if callback:
                        callback()
                    imgui.close_current_popup()
                    closed = True
                imgui.same_line()
    else:
        opened = 0
        closed = True
    return opened, closed


def rand_num_str(len=8):
    return "".join((random.choice('0123456789') for _ in range(len)))


def push_popup(gui, *args, bottom=False, **kwargs):
    if len(args) + len(kwargs) > 1:
        if args[0] is popup or args[0] is msgbox.msgbox:
            args = list(args)
            args[1] = args[1] + "##popup_" + rand_num_str()
        popup_func = functools.partial(*args, **kwargs)
    else:
        popup_func = args[0]
    if bottom:
        gui.popup_stack.insert(0, popup_func)
    else:
        gui.popup_stack.append(popup_func)
    return popup_func


def my_checkbox(label: str, state: bool, frame_size: tuple=None, do_vertical_align=True):
    style = imgui.get_style()
    if state:
        imgui.push_style_color(imgui.Col_.frame_bg_hovered, style.color_(imgui.Col_.button_hovered))
        imgui.push_style_color(imgui.Col_.frame_bg, style.color_(imgui.Col_.button_hovered))
        imgui.push_style_color(imgui.Col_.check_mark, style.color_(imgui.Col_.text))
    if frame_size is not None:
        frame_padding = [style.frame_padding.x, style.frame_padding.y]
        imgui.push_style_var(imgui.StyleVar_.frame_padding, frame_size)
        imgui.push_style_var(imgui.StyleVar_.item_spacing, (0.,0.))
        imgui.begin_group()
        if do_vertical_align:
            imgui.dummy((0,frame_padding[1]))
        imgui.dummy((frame_padding[0],0))
        imgui.same_line()
    result = imgui.checkbox(label, state)
    if frame_size is not None:
        imgui.end_group()
        imgui.pop_style_var(2)
    if state:
        imgui.pop_style_color(3)
    return result
