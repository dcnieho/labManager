import pathlib
from imgui_bundle import imgui, icons_fontawesome

from . import filepicker, utils

class FileCommander:
    default_flags: int = (
        imgui.WindowFlags_.no_collapse |
        imgui.WindowFlags_.no_saved_settings
    )

    def __init__(self, title = "File commander", start_dir_left: str | pathlib.Path = None, start_dir_right: str | pathlib.Path = None, file_action_provider_args=None):
        self.title = title

        self.left  = filepicker.FilePicker(start_dir=start_dir_left , file_action_provider_args=file_action_provider_args)
        self.right = filepicker.FilePicker(start_dir=start_dir_right, file_action_provider_args=file_action_provider_args)
        self.right._listing_cache = self.left._listing_cache    # share listing cache

        # shared popup stack
        self.popup_stack = []
        self.left.popup_stack  = self.popup_stack
        self.right.popup_stack = self.popup_stack

    def draw(self):
        imgui.begin_child('##filecommander')

        space = imgui.get_content_region_avail()
        button_text_size = imgui.calc_text_size(icons_fontawesome.ICON_FA_BAN+" Cancel")
        bottom_margin = button_text_size.y+imgui.get_style().frame_padding.y*2+imgui.get_style().item_spacing.y
        space.y = -bottom_margin

        imgui.begin_child('##left_picker',size=(space.x*.4,space.y))
        self.left.draw_top_bar()
        self.left.draw_listing(leave_space_for_bottom_bar=False)
        imgui.end_child()
        imgui.same_line()
        imgui.begin_child('##actions',size=(space.x*.2,space.y))
        imgui.text('button')
        imgui.end_child()
        imgui.same_line()
        imgui.begin_child('##right_picker',size=(space.x*.4,space.y))
        self.right.draw_top_bar()
        self.right.draw_listing(leave_space_for_bottom_bar=False)
        imgui.end_child()

        closed = False
        if imgui.button(icons_fontawesome.ICON_FA_CHECK+" Done"):
            imgui.close_current_popup()
            closed = True
        imgui.end_child()
        return closed

    def tick(self):
        # Setup popup
        if not imgui.is_popup_open(self.title):
            imgui.open_popup(self.title)
        opened = 1
        size = imgui.get_io().display_size
        size.x *= .95
        size.y *= .95
        imgui.set_next_window_size(size, cond=imgui.Cond_.appearing)
        if imgui.begin_popup_modal(self.title, True, flags=self.default_flags)[0]:
            closed = utils.close_weak_popup(check_click_outside=False)
            closed2 = self.draw()
            closed    = closed    or closed2
        else:
            opened = 0
            closed = True

        utils.handle_popup_stack(self.popup_stack)
        return opened, closed
