from __future__ import annotations

import asyncio
import pathlib
from imgui_bundle import imgui, icons_fontawesome

from . import filepicker, utils
from .... import GUI
from .... import master

class FileCommander:
    default_flags: int = (
        imgui.WindowFlags_.no_collapse |
        imgui.WindowFlags_.no_saved_settings
    )

    def __init__(self, mainGUI: GUI.MainGUI, master: master.Master, selected_clients: dict[int, bool], title = "File commander", start_dir_left: str | pathlib.Path = None, start_dir_right: str | pathlib.Path = None, file_action_provider_args=None):
        self.mainGUI = mainGUI
        self.master = master
        self.selected_clients = selected_clients

        self.title = title

        # get first selected client, open file pickers to that location
        with self.master.clients_lock:
            client_name = next(self.master.clients[c].name for c in self.selected_clients if self.selected_clients[c])
        self.left  = filepicker.FilePicker(start_machine=client_name, start_dir=start_dir_left , file_action_provider_args=file_action_provider_args)
        self.right = filepicker.FilePicker(start_machine=client_name, start_dir=start_dir_right, file_action_provider_args=file_action_provider_args)
        self.right._listing_cache = self.left._listing_cache    # share listing cache
        self.right.allow_selecting_machine = False
        self.left_machine = self.left.machine
        # route remote actions through us
        self.left.file_action_provider.remote_action_provider   = self.remote_action_provider
        self.right.file_action_provider.remote_action_provider  = self.remote_action_provider

        # disable keyboard navigation as we don't know which of the file pickers has focus upon key presses
        self.left.disable_keyboard_navigation = True
        self.right.disable_keyboard_navigation = True

        # GUI state
        self.append_station_name = False
        # shared popup stack
        self.popup_stack = []
        self.left.popup_stack  = self.popup_stack
        self.right.popup_stack = self.popup_stack


    def draw(self):
        imgui.begin_child('##filecommander')
        with self.master.clients_lock:
            selected_clients = [c for c in self.selected_clients if self.selected_clients[c]]
        computers_txt = ', '.join((self.master.clients[i].name for i in selected_clients))
        imgui.text_wrapped('The action you do in this interface will be performed on the following computers: '+computers_txt)

        # figure out layout: get width of middle section, divide leftover equally between the other two
        # widest element is checkbox, get its width
        cb_label = 'Append station folder?'
        w = imgui.get_frame_height() + imgui.get_style().item_inner_spacing.x + imgui.calc_text_size(cb_label).x + 2*imgui.get_style().frame_padding.x

        space = imgui.get_content_region_avail()
        button_text_size = imgui.calc_text_size(icons_fontawesome.ICON_FA_BAN+" Cancel")
        bottom_margin = button_text_size.y+imgui.get_style().frame_padding.y*2+imgui.get_style().item_spacing.y
        space.y = -bottom_margin

        imgui.begin_child('##left_picker',size=((space.x-w)/2,space.y))
        self.left.draw_top_bar()
        self.left.draw_listing(leave_space_for_bottom_bar=False)
        imgui.end_child()
        imgui.same_line()

        imgui.begin_child('##actions',size=(w,space.y))
        enabled = self.left.machine.startswith(filepicker.FileActionProvider.remote_prefix) and self.right.machine.startswith(filepicker.FileActionProvider.remote_prefix)
        enabled = enabled and any(self.left.selected.values())
        if not enabled:
            utils.push_disabled()
        imgui.push_font(self.mainGUI.icon_font)
        # get button size, center horizontally and vertically
        button_text_size = imgui.calc_text_size(icons_fontawesome.ICON_FA_ARROW_RIGHT)
        button_size = [t+p*2 for t,p in zip(button_text_size,imgui.get_style().frame_padding)]#+imgui.get_style().item_spacing.y
        margin = [(a-b)/2 for a,b in zip(imgui.get_content_region_avail(), button_size)]
        imgui.set_cursor_pos([a+b for a,b in zip(imgui.get_cursor_pos(),margin)])
        imgui.button(icons_fontawesome.ICON_FA_ARROW_RIGHT+"##action")
        imgui.pop_font()
        # center checkbox+label horizontally
        imgui.set_cursor_pos_x(imgui.get_cursor_pos_x()+(imgui.get_content_region_avail().x-w)/2)
        self.append_station_name = imgui.checkbox(cb_label,self.append_station_name)[1]
        if not enabled:
            utils.pop_disabled()
        utils.draw_hover_text('When enabled, a folder will be made for each machine to receive the files copied from the left side. That means you won\'t have all files mixed together, but organized in folders per machine.', text='', hovered_flags=imgui.HoveredFlags_.allow_when_disabled)
        imgui.end_child()
        imgui.same_line()

        imgui.begin_child('##right_picker',size=((space.x-w)/2,space.y))
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
        # check if either of the file pickers needs a refresh
        if not self.left.refreshing and (self.left.elapsed>2 or imgui.is_key_pressed(imgui.Key.f5)):
            self.left.refresh()
        if not self.right.refreshing and (self.right.elapsed>2 or imgui.is_key_pressed(imgui.Key.f5)):
            self.right.refresh()
        # if left machine changed, also change for right
        if self.left.machine!=self.left_machine:
            self.right.goto(self.left.machine,'root')
            self.left_machine = self.left.machine

        # Setup popup
        if not imgui.is_popup_open(self.title):
            imgui.open_popup(self.title)
        opened = 1
        size = imgui.get_io().display_size
        size.x *= .95
        size.y *= .95
        imgui.set_next_window_size(size, cond=imgui.Cond_.appearing)
        if imgui.begin_popup_modal(self.title, True, flags=self.default_flags)[0]:
            closed  = utils.close_weak_popup(check_click_outside=False)
            closed2 = self.draw()
            closed  = closed or closed2
        else:
            opened = 0
            closed = True

        utils.handle_popup_stack(self.popup_stack)
        return opened, closed

    async def remote_action_provider(self, action: str, path: pathlib.Path, path2: pathlib.Path|None = None):
        # got an action, route to all selected clients
        coros = []
        for c in (c for c in self.selected_clients if self.selected_clients[c]):
            match action:
                case 'make_dir':
                    coros.append(self.master.make_client_folder(self.master.clients[c], path))
                case 'rename_path':
                    coros.append(self.master.rename_client_file_folder(self.master.clients[c], path, path2))
                case 'delete_path':
                    coros.append(self.master.delete_client_file_folder(self.master.clients[c], path))

        await asyncio.gather(*coros)