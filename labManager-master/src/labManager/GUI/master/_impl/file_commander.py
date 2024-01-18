from __future__ import annotations

import asyncio
import pathlib
from dataclasses import dataclass
from imgui_bundle import hello_imgui, imgui, icons_fontawesome

from labManager.common import async_thread, structs
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
            client_name = next(self.master.clients[c].name for c in self.selected_clients if self.selected_clients[c] and c in self.master.clients and self.master.clients[c].online)
        file_action_provider = filepicker.FileActionProvider(**file_action_provider_args)   # share file action provider
        self.left  = filepicker.FilePicker(start_machine=client_name, start_dir=start_dir_left , file_action_provider=file_action_provider)
        self.right = filepicker.FilePicker(start_machine=client_name, start_dir=start_dir_right, file_action_provider=file_action_provider)
        self.right._listing_cache = self.left._listing_cache                # share listing cache
        # right pane cannot chose machine, we track machine selected on the left and force right to match
        self.right.allow_selecting_machine = False
        self.left_machine = self.left.machine
        # don't show local machine
        self.left.show_local_machine  = False
        self.right.show_local_machine = False
        # route remote actions through us
        self.left.file_action_provider.remote_action_provider   = self.remote_action_provider

        # disable keyboard navigation as we don't know which of the file pickers has focus upon key presses
        self.left.disable_keyboard_navigation = True
        self.right.disable_keyboard_navigation = True

        # GUI state
        self.append_computer_name = False
        self._is_popup = False
        self.win_name: str = '' # caller should set this so that we can close ourselves when running as window (_is_popup==False). If not set, done button does nothing
        # shared popup stack
        self.popup_stack = []
        self.left.popup_stack  = self.popup_stack
        self.right.popup_stack = self.popup_stack


    def draw(self):
        # check if either of the file pickers needs a refresh
        if not self.left.refreshing and (self.left.elapsed>2 or imgui.is_key_pressed(imgui.Key.f5)):
            self.left.refresh()
        if not self.right.refreshing and (self.right.elapsed>2 or imgui.is_key_pressed(imgui.Key.f5)):
            self.right.refresh()
        # if left machine changed, also change for right
        if self.left.machine!=self.left_machine:
            self.right.goto(self.left.machine,'root')
            self.left_machine = self.left.machine

        imgui.begin_child('##filecommander')
        with self.master.clients_lock:
            selected_clients = [c for c in self.selected_clients if self.selected_clients[c] and c in self.master.clients and self.master.clients[c].online]
            computers_txt = ', '.join((self.master.clients[i].name for i in selected_clients))

        # figure out layout
        space = imgui.get_content_region_avail()
        button_text_size = imgui.calc_text_size(icons_fontawesome.ICON_FA_BAN+" Cancel")
        bottom_margin = button_text_size.y+imgui.get_style().frame_padding.y*2+imgui.get_style().item_spacing.y
        space.y = -bottom_margin
        if not selected_clients:
            imgui.begin_child('##nothing-to-do',size=space)
            imgui.text_wrapped('You do not have any running clients selected in the main GUI, nothing to do here')
            imgui.end_child()
        else:
            imgui.text_wrapped('The action you do in this interface will be performed on the following computers: '+computers_txt)

            # figure out layout: get width of middle section, divide leftover equally between the other two
            # widest element is checkbox, get its width
            cb_label = 'Append station folder?'
            w = imgui.get_frame_height() + imgui.get_style().item_inner_spacing.x + imgui.calc_text_size(cb_label).x + 2*imgui.get_style().frame_padding.x

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
            if imgui.button(icons_fontawesome.ICON_FA_ARROW_RIGHT+"##action"):
                self.do_copy()
            imgui.pop_font()
            # center checkbox+label horizontally
            imgui.set_cursor_pos_x(imgui.get_cursor_pos_x()+(imgui.get_content_region_avail().x-w)/2)
            self.append_computer_name = imgui.checkbox(cb_label,self.append_computer_name)[1]
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
            if self._is_popup:
                imgui.close_current_popup()
            else:
                if win := hello_imgui.get_runner_params().docking_params.dockable_window_of_name(self.win_name):
                    win.is_visible = False
            closed = True
        imgui.end_child()

        utils.handle_popup_stack(self.popup_stack)

        return closed

    def get_desired_size(self):
        size = imgui.get_io().display_size
        size.x *= .95
        size.y *= .95
        return size

    def tick(self):
        # for running as a popup
        self._is_popup = True

        # Setup popup
        if not imgui.is_popup_open(self.title):
            imgui.open_popup(self.title)
        opened = 1
        size = self.get_desired_size()
        imgui.set_next_window_size(size, cond=imgui.Cond_.appearing)
        if imgui.begin_popup_modal(self.title, True, flags=self.default_flags)[0]:
            closed  = utils.close_weak_popup(check_click_outside=False)
            closed2 = self.draw()
            closed  = closed or closed2
        else:
            opened = 0
            closed = True

        return opened, closed

    def do_copy(self):
        # get info
        with self.left.items_lock:
            sources = [self.left.items[c] for c in self.left.sorted_items if self.left.selected[c]]
            source_paths = [s.full_path for s in sources]
            source_paths_disp = [s.display_name for s in sources]
        dest = self.right.loc
        with self.master.clients_lock:
            clients = [c for c in self.selected_clients if self.selected_clients[c] and c in self.master.clients and self.master.clients[c].online]
            computers_txt = '\n  '.join((self.master.clients[i].name for i in clients))

        sources_disp = '\n  '.join(source_paths_disp)
        def _confirmation_popup():
            extra = "\\<computer_name>" if self.append_computer_name else ""
            imgui.text(f'This will copy the following items:\n  {sources_disp}\nfrom the path {source_paths[0].parent}\ninto the folder {dest}{extra}\non each of the following computers:\n  {computers_txt}\nContinue?')
            return 0 if imgui.is_key_released(imgui.Key.enter) else None

        async def _do_it():
            nonlocal clients

            @dataclass(frozen=True)
            class CopyTask:
                client: int
                task: asyncio.Task | asyncio.Future
                is_make_folder: bool = False

            tasks: set[CopyTask] = set()
            launched_make_folder = False
            clients_for_copy = []
            # somewhat complicated logic so that we do not get stuck if for some
            # reason a client doesn't do the make_folder action. For each client,
            # once the folder is made, we launch the copy action
            while True:
                # first make folders with computer name
                if self.append_computer_name and not launched_make_folder:
                    coros   = []
                    for c in clients:
                        dest_path = dest / self.master.clients[c].name
                        coros.append(self.master.make_client_folder(self.master.clients[c], dest_path, exist_ok=True))
                    action_ids = await asyncio.gather(*coros)

                    # get waiters and wait for them to complete
                    for c,aid in zip(clients,action_ids):
                        if c in self.master.clients and self.master.clients[c].online and aid in self.master.clients[c].online.file_actions:
                            tsk = CopyTask(c, self.master.add_waiter('file-action', aid), is_make_folder=True)
                            tasks.add(tsk)
                    launched_make_folder = True
                else:
                    clients_for_copy = clients

                # launch copy actions
                if clients_for_copy:
                    for c in clients_for_copy:
                        if self.append_computer_name:
                            dest_path = dest / self.master.clients[c].name
                        else:
                            dest_path = dest
                        for s in source_paths:
                            d = dest_path / s.name
                            # NB: this just launches the task, doesn't wait for it to finish
                            # which is good, user can see results in file action GUI
                            tsk = CopyTask(c, asyncio.create_task(self.master.copy_client_file_folder(self.master.clients[c], s, d, dirs_exist_ok=True)))
                            tasks.add(tsk)
                    clients_for_copy = []

                done, _ = await asyncio.wait([t.task for t in tasks], timeout=0.1)

                # check what's done and if there is any need for follow up
                for t in done:
                    # find task
                    for tsk in tasks:
                        if tsk.task==t:
                            my_task = tsk
                            break
                    tasks.discard(my_task)
                    # check if its a make folder or a copy action to determine next action
                    if my_task.is_make_folder:
                        # check result of make folder, launch copy if succeeded
                        c = my_task.client
                        if c in self.master.clients and self.master.clients[c].online and aid in self.master.clients[c].online.file_actions:
                            result = self.master.clients[c].online.file_actions[aid]
                            if result['status']==structs.Status.Finished:
                                clients_for_copy.append(c)
                    else:
                        # nothing to do for file copy action
                        pass

                # we're done if there is nothing more to wait for or to schedule
                if not tasks and not clients_for_copy:
                    break

        buttons = {
            icons_fontawesome.ICON_FA_CHECK+" Yes": lambda: async_thread.run(_do_it()),
            icons_fontawesome.ICON_FA_BAN+" Cancel": None
        }
        utils.push_popup(self, lambda: utils.popup("Confirm copy", _confirmation_popup, buttons = buttons, closable=True))

    async def remote_action_provider(self, action: str, path: pathlib.Path, path2: pathlib.Path|None = None):
        # got an action, route to all selected clients
        coros = []
        for c in (c for c in self.selected_clients if self.selected_clients[c] and c in self.master.clients and self.master.clients[c].online):
            match action:
                case 'make_dir':
                    coros.append(self.master.make_client_folder(self.master.clients[c], path))
                case 'rename_path':
                    coros.append(self.master.rename_client_file_folder(self.master.clients[c], path, path2))
                case 'delete_path':
                    coros.append(self.master.delete_client_file_folder(self.master.clients[c], path))

        await asyncio.gather(*coros)