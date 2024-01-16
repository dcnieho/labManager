import pathlib
import typing
from imgui_bundle import hello_imgui, imgui, icons_fontawesome, imspinner
import sys
import natsort
import asyncio
import concurrent
import threading
from dataclasses import dataclass
from typing import Awaitable, Any, Callable

from labManager.common import async_thread, file_actions, structs
from labManager.common.network import nmb
from . import msgbox, utils
from .... import master


DRIVE_ICON = icons_fontawesome.ICON_FA_HDD
SERVER_ICON = icons_fontawesome.ICON_FA_SERVER
DIR_ICON = icons_fontawesome.ICON_FA_FOLDER
FILE_ICON = icons_fontawesome.ICON_FA_FILE


@dataclass
class DirEntryWithCache(structs.DirEntry):
    # display fields
    display_name: str = ''
    ctime_str: str = None
    mtime_str: str = None
    size_str: str = None

    def __init__(self, item: structs.DirEntry):
        super().__init__(item.name, item.is_dir, item.full_path, item.ctime, item.mtime, item.size, item.mime_type, item.extra)

        # prep display strings
        if self.mime_type and self.mime_type.startswith('labManager/drive'):
            icon = DRIVE_ICON
        elif self.mime_type=='labManager/net_name':
            icon = SERVER_ICON
        elif self.is_dir:
            icon = DIR_ICON
        else:
            icon = FILE_ICON
        self.display_name   = icon + "  " + self.name

        self.ctime_str      = self.ctime.strftime("%Y-%m-%d %H:%M:%S") if self.ctime else None
        self.mtime_str      = self.mtime.strftime("%Y-%m-%d %H:%M:%S") if self.mtime else None

        # size
        if not self.is_dir or (self.mime_type and self.mime_type.startswith('labManager/drive')):
            i = 0
            units = ['B', 'KiB', 'MiB', 'GiB', 'TiB']
            size = self.size
            while size>1024:
                i+=1
                size /= 1024
            if i==0:
                self.size_str = f'{size:.0f} {units[i]}'
            else:
                self.size_str = f'{size:.1f} {units[i]}'

class RemoteNotFound(Exception):
    ...

class FileActionProvider:
    def __init__(self, listing_callback: Callable[[str, str|pathlib.Path, list[structs.DirEntry]|Exception], None]=None, action_callback: Callable[[str, pathlib.Path, str, pathlib.Path|Exception], None] = None, master: master.Master = None, network: str = None):
        self.waiters: set[concurrent.futures.Future] = set()

        self.listing_callbacks: list[Callable[[list[structs.DirEntry]|Exception, bool], None]] = []
        if listing_callback:
            self.listing_callbacks.append(listing_callback)
        self.action_callbacks:  list[Callable[[pathlib.Path|Exception, str], None]] = []
        if action_callback:
            self.action_callback.append(action_callback)
        self.remote_disconnected_callbacks: list[Callable[[structs.ConnectedClient, int],None]] = []

        self.master = master
        # install hook
        self.master.add_hook('client_disconnected', self._remote_disconnected)

        # remote action provider. If set, any actions on remotes are routed
        # through this provider. If not, self.master member functions are used
        self.remote_action_provider: Callable[[str, pathlib.Path, pathlib.Path|None], Awaitable[int|None]] = None

        self.network: str|None = network
        self.network_computer_getter: nmb.NetBIOSDiscovery = None
        self.network_computer_getter_task: asyncio.Task = None
        if self.network:
            self.network_computer_getter = nmb.NetBIOSDiscovery(self.network, 30)
            self.network_computer_getter_task = async_thread.loop.create_task(self.network_computer_getter.run())

    def __del__(self):
        if self.network_computer_getter_task and not self.network_computer_getter_task.done():
            self.network_computer_getter_task.cancel()
        for w in self.waiters:
            if not w.done():
                w.cancel()

    def supports_remote(self):
        return True if self.master else False

    def get_remotes(self) -> dict[int,str]:
        if not self.supports_remote():
            return {}
        with self.master.clients_lock:
            remotes = {c:self.master.clients[c].name for c in self.master.clients if self.master.clients[c].online}
        return remotes

    local_name    = 'This PC'
    remote_prefix = 'machine: remote: '
    @staticmethod
    def get_full_machine_name(name: str):
        if name==FileActionProvider.local_name:
            return 'machine: local'
        else:
            # assume remote
            return FileActionProvider.remote_prefix + name.strip()

    @staticmethod
    def get_machine_name(full_name: str):
        if full_name==FileActionProvider.get_full_machine_name(FileActionProvider.local_name):
            return FileActionProvider.local_name
        else:
            # assume remote
            return full_name.removeprefix(FileActionProvider.remote_prefix).strip()

    def resolve_machine(self, machine: str|None) -> tuple[str, bool, int|None]:
        is_local = True
        client_id = None
        full_local_name = self.get_full_machine_name(self.local_name)
        if machine in [None,self.local_name,full_local_name]:
            machine = full_local_name
        else:
            machine = machine.removeprefix(self.remote_prefix).strip()
            for cid, name in self.get_remotes().items():
                if name==machine:
                    client_id = cid
            if client_id is None:
                raise RemoteNotFound(f"Machine {machine} was not found among connected machines")
            is_local = False
            machine = self.get_full_machine_name(machine)
        return machine, is_local, client_id

    def _remote_disconnected(self, client: structs.ConnectedClient, client_id: int):
        with self.master.clients_lock:
            remote_name = self.master.clients[client_id].name
        remote_full_name = self.get_full_machine_name(remote_name)
        for c in self.remote_disconnected_callbacks:
            c(remote_name, remote_full_name)


    def get_listing(self, machine: str, path: str|pathlib.Path) -> list[structs.DirEntry]|concurrent.futures.Future:
        try:
            machine, is_local, client_id = self.resolve_machine(machine)
        except RemoteNotFound as exc:
            machine = self.get_full_machine_name(self.get_machine_name(machine))
            self._listing_done(exc, machine, path)
            return

        fut = None
        if is_local:
            if path=='root':
                try:
                    result = file_actions.get_drives()
                    result.extend(file_actions.get_thispc_listing())
                    if self.network_computer_getter:
                        network_computers = self.network_computer_getter.get_machines(as_direntry=True)
                        result.extend([m for m,_ in network_computers])
                except Exception as exc:
                    result = exc
                self._listing_done(result, machine, 'root')
            else:
                # check whether this is a path to a network computer (e.g. \\SERVER)
                net_comp = file_actions.get_net_computer(path)
                try:
                    if net_comp:
                        # network computer name, get its shares
                        result = file_actions.get_visible_shares(net_comp,'Guest','')
                    else:
                        # normal directory or share on a network computer, no special handling needed
                        result = file_actions.get_dir_list_sync(path)
                except Exception as exc:
                    result = exc
                self._listing_done(result, machine, path)
        else:
            if not self.supports_remote():
                self._listing_done(ValueError(f'Remote machine selected ("{machine}") but not supported'), machine, path)
            if path=='root':
                async_thread.run(self.master.get_client_drives(self.master.clients[client_id]))
            else:
                # check whether this is a path to a network computer (e.g. \\SERVER)
                net_comp = file_actions.get_net_computer(path)
                if net_comp:
                    # network computer name, get its shares
                    async_thread.run(self.master.get_client_remote_shares(self.master.clients[client_id],net_comp,'Guest',''))
                    path = f'//{net_comp}/'
                else:
                    # normal directory or share on a network computer, no special handling needed
                    async_thread.run(self.master.get_client_file_listing(self.master.clients[client_id],path))
            fut = async_thread.run(asyncio.wait_for(self.master.add_waiter('file-listing', path, client_id), timeout=None), lambda f: self._listing_done(f, machine, path))
        if fut:
            self.waiters.add(fut)
        return fut

    def _listing_done(self, fut: concurrent.futures.Future|list[structs.DirEntry], machine: str, path: str|pathlib.Path):
        result = self._get_result_from_future(fut)
        if result=='cancelled':
            return  # nothing more to do

        if not self.listing_callbacks:
            return

        try:
            machine, is_local, client_id = self.resolve_machine(machine)
        except RemoteNotFound as exc:
            result = exc
        else:
            if is_local:
                if result is None:
                    return
            else:
                if not self.supports_remote():
                    result = None
                else:
                    # retrieve result
                    result = self.master.clients[client_id].online.file_listings[str(path)]['listing']
        # call callback
        for c in self.listing_callbacks:
            c(machine, path, result)

    def make_dir(self, machine: str, path: pathlib.Path):
        action = 'make_dir'
        try:
            machine, is_local, client_id = self.resolve_machine(machine)
        except RemoteNotFound as exc:
            machine = self.get_full_machine_name(self.get_machine_name(machine))
            self._action_done(exc, machine, path, action)
        else:
            if is_local:
                fut = async_thread.run(file_actions.make_dir(path), lambda f: self._action_done(f, machine, path, action))
            else:
                if not self.supports_remote():
                    self._action_done(ValueError(f'Remote machine selected ("{machine}") but not supported'), machine, path, action)
                if self.remote_action_provider:
                    fut = async_thread.run(self.remote_action_provider(action, path), lambda f: self._finish_remote_action(f, client_id, machine, path, action))
                else:
                    fut = async_thread.run(self.master.make_client_folder(self.master.clients[client_id], path), lambda f: self._finish_remote_action(f, client_id, machine, path, action))
            self.waiters.add(fut)
            return fut

    def rename_path(self, machine: str, old_path: pathlib.Path, new_path: pathlib.Path):
        action = 'rename_path'
        try:
            machine, is_local, client_id = self.resolve_machine(machine)
        except RemoteNotFound as exc:
            machine = self.get_full_machine_name(self.get_machine_name(machine))
            self._action_done(exc, machine, old_path, action)
        else:
            if is_local:
                fut = async_thread.run(file_actions.rename_path(old_path, new_path), lambda f: self._action_done(f, machine, old_path, action))
            else:
                if not self.supports_remote():
                    self._action_done(ValueError(f'Remote machine selected ("{machine}") but not supported'), machine, old_path, action)
                if self.remote_action_provider:
                    fut = async_thread.run(self.remote_action_provider(action, old_path, new_path), lambda f: self._finish_remote_action(f, client_id, machine, old_path, action))
                else:
                    fut = async_thread.run(self.master.rename_client_file_folder(self.master.clients[client_id], old_path, new_path), lambda f: self._finish_remote_action(f, client_id, machine, old_path, action))
            self.waiters.add(fut)
            return fut

    def delete_path(self, machine: str, path: pathlib.Path):
        action = 'delete_path'
        try:
            machine, is_local, client_id = self.resolve_machine(machine)
        except RemoteNotFound as exc:
            machine = self.get_full_machine_name(self.get_machine_name(machine))
            self._action_done(exc, machine, path, action)
        else:
            if is_local:
                fut = async_thread.run(file_actions.delete_path(path), lambda f: self._action_done(f, machine, path, action))
            else:
                if not self.supports_remote():
                    self._action_done(ValueError(f'Remote machine selected ("{machine}") but not supported'), machine, path, action)
                if self.remote_action_provider:
                    fut = async_thread.run(self.remote_action_provider(action, path), lambda f: self._finish_remote_action(f, client_id, machine, path, action))
                else:
                    fut = async_thread.run(self.master.delete_client_file_folder(self.master.clients[client_id], path), lambda f: self._finish_remote_action(f, client_id, machine, path, action))
            self.waiters.add(fut)
            return fut

    def _finish_remote_action(self, fut: concurrent.futures.Future, client_id: int, machine: str, path: pathlib.Path, action: str):
        action_id = self._get_result_from_future(fut)
        if action_id is None or action_id=='cancelled':
            return  # nothing more to do

        # we should have a file_action_id now, enqueue waiter that calls action_done
        fut = async_thread.run(asyncio.wait_for(self.master.add_waiter('file-action', action_id), timeout=None), lambda f: self._action_done(f, machine, path, action, client_id, action_id))
        self.waiters.add(fut)

    def _action_done(self, fut: concurrent.futures.Future, machine: str, path: pathlib.Path, action: str, client_id: int=None, action_id: int=None):
        result = self._get_result_from_future(fut)
        if result=='cancelled':
            return  # nothing more to do

        if not self.action_callbacks:
            return

        # get result and call callback
        if client_id and self.master and client_id in self.master.clients and action_id in self.master.clients[client_id].online.file_actions:
            result = self.master.clients[client_id].online.file_actions[action_id]
            if result['status']==structs.Status.Errored:
                result = result['error']
                if isinstance(result,str):
                    result = RuntimeError(result)
            elif 'return_path' in result:
                result = result['return_path']
            else:
                result = None
        for c in self.action_callbacks:
            c(machine, path, action, result)

    def _get_result_from_future(self, fut: concurrent.futures.Future|list[structs.DirEntry]) -> list[structs.DirEntry]:
        if isinstance(fut, concurrent.futures.Future):
            self.waiters.discard(fut)
            try:
                return fut.result()
            except concurrent.futures.CancelledError:
                return 'cancelled'
            except Exception as exc:
                return exc
        else:
            return fut


class FilePicker:
    default_flags: int = (
        imgui.WindowFlags_.no_collapse |
        imgui.WindowFlags_.no_saved_settings
    )

    def __init__(self, title="File picker", start_machine: str = None, start_dir: str | pathlib.Path = None, callback: typing.Callable = None, allow_multiple = True, file_action_provider: FileActionProvider=None, custom_popup_flags=0):
        self.title = title
        self.elapsed = 0.0
        self.callback = callback
        if file_action_provider:
            self.file_action_provider = file_action_provider
        else:
            self.file_action_provider = FileActionProvider()
        file_action_provider.listing_callbacks.append(self._listing_done)
        file_action_provider.action_callbacks .append(self._action_done)
        file_action_provider.remote_disconnected_callbacks.append(self._remote_lost)

        self._listing_cache: dict[tuple(str,str|pathlib.Path), dict[int,DirEntryWithCache]] = {}
        self.popup_stack = []
        self.dialog_provider = DialogProvider(self, self._launch_action)
        self.disable_keyboard_navigation = False

        self.items: dict[int, DirEntryWithCache] = {}
        self.selected: dict[int, bool] = {}
        self.items_lock: threading.Lock = threading.Lock()  # for self.items and self.selected
        self.allow_multiple = allow_multiple
        self.msg: str = None
        self.filter_box_text = ''
        self.require_sort = False
        self.sorted_items: list[int] = []
        self.last_clicked_id: int = None

        self.machine: str = None
        self.allow_selecting_machine = True
        self.show_local_machine = True  # show local machine in list of machines? Its callers responsibility to set a valid remote machine so that this interface doesn't open showing the local machine
        self.loc: pathlib.Path = None
        self.refreshing = False
        self.new_loc = False
        self.history: list[tuple[str,str|pathlib.Path]] = []
        self.history_loc = -1
        self.path_bar_popup: dict[str,Any] = {}
        self.predicate = None
        self.default_flags = custom_popup_flags or FilePicker.default_flags
        self.platform_is_windows = sys.platform.startswith("win")

        if start_machine and not start_dir:
            if not self.file_action_provider.resolve_machine(start_machine)[1]:
                # remote machine, default to root as current path ('.') would make no sense
                start_dir = 'root'
        self.goto(start_machine or self.file_action_provider.local_name, start_dir or '.')
        self._request_listing(self.machine, 'root')   # request root listing so we have the drive names

    def goto(self, machine: str, path: str | pathlib.Path, add_history=True):
        is_root = False
        if isinstance(path, str):
            if path.casefold()==self._get_path_display_name(machine, 'root').casefold():
                path = 'root'
            is_root = path=='root'

        try:
            machine, is_local, _ = self.file_action_provider.resolve_machine(machine)
        except RemoteNotFound:
            machine = self.file_action_provider.get_full_machine_name(self.file_action_provider.get_machine_name(machine))
            is_local = False
        if not is_root:
            if (comp := file_actions.get_net_computer(path)):
                # ensure loc has the format //SERVER/, which is what pathlib understands
                # but keep it as a string, these things don't round-trip in pathlib
                path = f'//{comp}/'
            else:
                path = pathlib.Path(path)
                if is_local and path.is_file():
                    path = path.parent
                if path is None:
                    path = pathlib.Path('.')
                if not str(path).startswith('\\\\') or str(path).startswith('//'):
                    # don't call resolve on network paths
                    path = path.resolve()

        if machine!=self.machine or path!=self.loc:
            if machine!=self.machine and not self.allow_selecting_machine:
                # delete history so we can't go back to other machine,
                # this is ok, new location is about to be appended
                add_history = True  # make sure we put this location in the history
                self.history.clear()
                self.history_loc = -1
            self.machine = machine
            self.loc = path
            self.new_loc = True
            if add_history:
                if self.history_loc>=0:
                    # remove any history after current, as we're appending a new location
                    del self.history[self.history_loc+1:]
                self.history.append((self.machine,self.loc))
                self.history_loc += 1
            # changing location clears selection
            with self.items_lock:
                utils.set_all(self.selected, False)
            # changing location clear filter box
            self.filter_box_text = ''
            # load from cache if available
            if (self.machine,self.loc) in self._listing_cache:
                self._update_listing(self.machine, self.loc, True)
            # load new directory
            self.refresh()

    def _remote_lost(self, remote_name: str, remote_full_name: str):
        if self.machine!=remote_full_name:
            # nothing to do
            return

        # stop any ongoing request so that a new request will be kicked off soon
        # when the client is actually fully gone (at this stage it is about to be
        # removed)
        self.refreshing = False

    def refresh(self):
        # launch refresh
        self.refreshing = True
        self._request_listing(self.machine, self.loc)

    def _request_listing(self, machine: str, path: str|pathlib.Path):
        self.file_action_provider.get_listing(machine, path)

    def _listing_done(self, machine: str, path: str|pathlib.Path, items: list[structs.DirEntry]|Exception):
        # deal with cache
        if not isinstance(items, Exception):
            items = {i:DirEntryWithCache(item) for i,item in enumerate(items)}
        self._listing_cache[(machine,path)] = items

        if str(path)==str(self.loc):
            # also load all parent paths if not in cache already, so path bar
            # drop downs work
            loc = self.loc
            while loc:
                if (machine,loc) not in self._listing_cache:
                    self._request_listing(machine, loc)
                loc = self._get_parent(loc)

            # and update the shown listing
            self._update_listing(machine, path, False)

    def _update_listing(self, machine: str, path: str|pathlib.Path, from_cache: bool):
        previously_selected = []
        with self.items_lock:
            if not self.new_loc:
                previously_selected = [self.items[iid].full_path for iid in self.items if iid in self.selected and self.selected[iid]]
            self.items.clear()
            self.selected.clear()
            self.msg = None
            items = self._listing_cache[(machine,path)]
            if isinstance(items, Exception):
                self.msg = f"Cannot open this folder!\n:{items}"
            else:
                self.items = items.copy()
                self.selected = {k:False for k in self.items}
                if not self.items:
                    self.msg = "This folder is empty!"

        # if refreshed the same directory, restore old selection
        self._select_paths(previously_selected)

        self.require_sort = True
        self.new_loc = False
        if not from_cache:
            self.refreshing = False
            self.elapsed = 0.0

    def _launch_action(self, action: str, path: str|pathlib.Path, path2: str|pathlib.Path = None):
        match action:
            case 'make_dir':
                self.file_action_provider.make_dir(self.machine, path)
            case 'rename_path':
                self.file_action_provider.rename_path(self.machine, path, path2)
            case 'delete_path':
                self.file_action_provider.delete_path(self.machine, path)

    def _action_done(self, machine: str, path: pathlib.Path, action: str, result: None|pathlib.Path|Exception):
        if isinstance(result, Exception):
            match action:
                case 'make_dir':
                    action_lbl = 'making the folder'
                case 'rename_path':
                    action_lbl = 'renaming the folder or file'
                case 'delete_path':
                    action_lbl = 'deleting the folder or file'
            utils.push_popup(self, msgbox.msgbox, "Action error", f'Something went wrong {action_lbl} {path} on machine "{self.file_action_provider.get_machine_name(machine)}":\n{result}', msgbox.MsgBox.error)

        # trigger refresh of parent path where actions occurred
        self._request_listing(machine, path.parent)
        # if there is a result path and it has a different parent than the action path, refresh that one too
        if isinstance(result, pathlib.Path) and result.parent!=path.parent:
            self._request_listing(machine, result.parent)


    def _select_paths(self, paths: list[pathlib.Path]):
        got_one = False
        with self.items_lock:
            for path in paths:
                for iid in self.items:
                    entry = self.items[iid]
                    if entry.full_path==path and (not self.predicate or self.predicate(iid)):
                        self.selected[iid] = True
                        got_one = True
                        break
                if not self.allow_multiple and got_one:
                    break

    def _get_parent(self, path: str | pathlib.Path):
        if isinstance(path,str) and path=='root':
            return None
        if not isinstance(path,pathlib.Path):
            path = pathlib.Path(path)
        parent = path.parent
        if parent==path:
            if isinstance(path,pathlib.PureWindowsPath):
                if (net_comps := file_actions.split_network_path(path)):
                    if len(net_comps)==1:
                        # network computer, one higher is list of drives and
                        # visible network computers, i.e., root
                        parent = 'root'
                    else:
                        parent = pathlib.Path(f'//{"/".join(net_comps[:-1])}/')
                else:
                    # if a normal local path, then this means we're in a drive root
                    parent = 'root'
            else:
                # TODO: non-Windows logic
                parent = 'root'
        return parent

    def _get_path_display_name(self, machine: str, path: str | pathlib.Path, for_edit=False):
        path_str = str(path)
        if path_str=='root':
            loc_str = self.file_action_provider.get_machine_name(machine)
        else:
            if (comp := file_actions.get_net_computer(path_str)):
                # pathlib.Path's str() doesn't do the right thing here, render it ourselves
                loc_str = f'\\\\{comp}'
            else:
                if not for_edit and isinstance(path,pathlib.Path) and self._get_parent(path)=='root' and (machine,'root') in self._listing_cache and isinstance(self._listing_cache[(machine,'root')],dict):
                    # this is a drive root, lookup name of this drive
                    # except when this is meant to be a string to be edited, then we
                    # just want the raw drive location string
                    loc_str = None
                    for _,l in self._listing_cache[(machine,'root')].items():
                        if l.full_path==path:
                            loc_str = l.name
                    if loc_str is None:
                        loc_str = path.drive
                else:
                    loc_str = path_str
        return loc_str

    def _get_path_leaf_display_name(self, machine: str, path: str | pathlib.Path):
        if isinstance(path, pathlib.Path) and self._get_parent(path)!='root':
            if (net_comps := file_actions.split_network_path(path)):
                if len(net_comps)==1:
                    disp_name = f'\\\\{net_comps[0]}'
                else:
                    disp_name = net_comps[-1]
            else:
                disp_name = path.name
                if not disp_name:
                    # disk root
                    disp_name = str(path)
        else:
            # special string
            disp_name = self._get_path_display_name(machine, path)
        return disp_name


    def draw(self):
        cancelled = closed = False

        imgui.begin_child('##filepicker')
        self.draw_top_bar()
        closed = self.draw_listing(leave_space_for_bottom_bar=True)
        cancelled, closed2 = self.draw_bottom_bar()
        imgui.end_child()

        closed = closed or closed2
        return cancelled, closed

    def draw_top_bar(self):
        self.elapsed += imgui.get_io().delta_time

        enable_keyboard_nav = not self.popup_stack and not self.disable_keyboard_navigation and not imgui.get_io().want_text_input   # no keyboard navigation in this GUI if a popup is open or key input taken elsewhere
        backspace_released  = imgui.is_key_pressed(imgui.Key.backspace)
        shift_down          = imgui.is_key_down(imgui.Key.im_gui_mod_shift)

        imgui.begin_group()
        # History back button
        disabled = self.history_loc<=0
        if disabled:
            utils.push_disabled()
        if imgui.button(icons_fontawesome.ICON_FA_ARROW_LEFT) or (not disabled and enable_keyboard_nav and backspace_released and not shift_down):
            self.history_loc -= 1
            self.goto(*self.history[self.history_loc], add_history=False)
        if self.history_loc>0: # don't just use disabled var as we may have just changed self.history_loc
            if imgui.is_item_hovered():
                utils.draw_tooltip(self._get_path_display_name(*self.history[self.history_loc-1]))
            if imgui.begin_popup_context_item(f"##history_back_context"):
                for i in range(self.history_loc-1,-1,-1):
                    m,p = self.history[i]
                    if imgui.selectable(self._get_path_display_name(m,p), False)[0]:
                        self.history_loc = i
                        self.goto(m,p, add_history=False)
                imgui.end_popup()
        if disabled:
            utils.pop_disabled()
        # History forward button
        imgui.same_line(spacing=imgui.get_style().item_spacing.x/4)
        disabled = self.history_loc+1>=len(self.history)
        if disabled:
            utils.push_disabled()
        if imgui.button(icons_fontawesome.ICON_FA_ARROW_RIGHT) or (not disabled and enable_keyboard_nav and backspace_released and shift_down):
            self.history_loc += 1
            self.goto(*self.history[self.history_loc], add_history=False)
        if self.history_loc+1<len(self.history): # don't just use disabled var as we may have just changed self.history_loc
            if imgui.is_item_hovered():
                utils.draw_tooltip(self._get_path_display_name(*self.history[self.history_loc+1]))
            if imgui.begin_popup_context_item(f"##history_forward_context"):
                for i in range(self.history_loc+1,len(self.history)):
                    m,p = self.history[i]
                    if imgui.selectable(self._get_path_display_name(m,p), False)[0]:
                        self.history_loc = i
                        self.goto(m,p, add_history=False)
                imgui.end_popup()
        if disabled:
            utils.pop_disabled()
        # Up button
        imgui.same_line(spacing=imgui.get_style().item_spacing.x/4)
        parent = self._get_parent(self.loc)
        disabled = parent is None or self.loc==parent
        if disabled:
            utils.push_disabled()
        if imgui.button(icons_fontawesome.ICON_FA_ARROW_UP):
            self.goto(self.machine,parent)
        if not disabled and imgui.is_item_hovered():
            utils.draw_tooltip(self._get_path_display_name(self.machine,parent))
        if disabled:
            utils.pop_disabled()
        # Refresh button
        imgui.same_line(spacing=imgui.get_style().item_spacing.x/2)
        if self.refreshing:
            button_text_size = imgui.calc_text_size(icons_fontawesome.ICON_FA_REDO)
            button_size = (button_text_size.x+imgui.get_style().frame_padding.x*2, button_text_size.y+imgui.get_style().frame_padding.y*2)
            symbol_size = imgui.calc_text_size("x").y/2
            spinner_radii = [x/22*symbol_size for x in [22, 16, 10]]
            lw = 3.5/22*symbol_size
            spinner_diam = 2*spinner_radii[0]+lw
            offset_x = (button_size[0]-spinner_diam)/2
            cp = imgui.get_cursor_pos()
            imgui.set_cursor_pos_x(cp.x+offset_x)
            imspinner.spinner_ang_triple(f'loadingSpinner', *spinner_radii, lw, c1=imgui.get_style().color_(imgui.Col_.text_selected_bg), c2=imgui.get_style().color_(imgui.Col_.text), c3=imgui.get_style().color_(imgui.Col_.text_selected_bg))
            imgui.set_cursor_pos(cp)
            imgui.dummy(button_size)
            utils.draw_hover_text(text='', hover_text='Refreshing...')
        else:
            if imgui.button(icons_fontawesome.ICON_FA_REDO):
                self.refresh()
        # Location bar
        imgui.same_line(spacing=imgui.get_style().item_spacing.x/2)
        # determine size
        space = imgui.get_content_region_avail().x
        if space>250/.3*hello_imgui.dpi_window_size_factor():
            filt_space = 250*hello_imgui.dpi_window_size_factor()
        else:
            filt_space = int(space*.3)
        imgui.set_next_item_width(-filt_space)
        self.draw_path_bar()
        # filter box
        imgui.same_line(spacing=imgui.get_style().item_spacing.x/2)
        imgui.set_next_item_width(imgui.get_content_region_avail().x)
        _, value = imgui.input_text_with_hint('##filter_box', f'Filter {self._get_path_leaf_display_name(self.machine,self.loc)}', self.filter_box_text)
        if value != self.filter_box_text:
            self.filter_box_text = value
            self.require_sort = True
        imgui.end_group()

    def draw_path_bar(self):
        # port of ImFileDialog's PathBar, edited and extended to work the way i like it
        win = imgui.internal.get_current_window()
        if win.skip_items:
            return

        iid = win.get_id('##path_bar')
        state = win.state_storage.get_int_ref(iid, 0)   # bit 1: button mode?, bit 2: hovered?, bit 3: set keyboard focus

        ctx = imgui.get_current_context()
        w = imgui.calc_item_width()
        ts = imgui.calc_text_size('x')
        pos = imgui.get_cursor_screen_pos()
        ui_pos = imgui.get_cursor_pos()
        bb = imgui.internal.ImRect(pos, (pos.x+w, pos.y+ts.y+2*imgui.get_style().frame_padding.y))

        if not state & 0b001:
            # buttons
            imgui.push_clip_rect(bb.min, bb.max, False)
            hovered = ctx.io.mouse_pos.x >= bb.min.x and ctx.io.mouse_pos.x <= bb.max.x and \
                      ctx.io.mouse_pos.y >= bb.min.y and ctx.io.mouse_pos.y <= bb.max.y
            clicked = hovered and imgui.is_mouse_released(imgui.MouseButton_.left)
            path_element_hc = False # are any other of the path boxes hovered or clicked?
            frame_col = imgui.get_color_u32(imgui.Col_.frame_bg_hovered if state & 0b010 else imgui.Col_.frame_bg)
            imgui.internal.render_frame(bb.min,bb.max,frame_col,True,imgui.get_style().frame_rounding)

            # get path components
            make_elem = lambda x, lbl: (lbl, x, imgui.calc_text_size(x).x)
            separator = '>'
            btn_list = []
            loc = self.loc
            while loc:
                btn_list.append(make_elem(self._get_path_leaf_display_name(self.machine,loc), loc))
                btn_list.append(make_elem(separator, 'sep'))
                loc = self._get_parent(loc)
            del btn_list[-1]    # remove last separator
            btn_list.reverse()
            # simply show machine as an icon, show machine name upon hover
            btn_list[0] = (btn_list[0][0], (icons_fontawesome.ICON_FA_DESKTOP, btn_list[0][1]), btn_list[0][2])

            # check if whole path fits, else shorten
            get_total_width = lambda x: sum([b[2] for b in x]) + len(btn_list)*2*imgui.get_style().frame_padding.x
            total_width = get_total_width(btn_list)
            btn_removed = []
            if total_width>w:
                ellipsis = make_elem('···', 'ellipsis')
                btn_list.insert(1, ellipsis)
                while total_width>w:
                    if len(btn_list)<=4:
                        # nothing more that can be removed
                        break
                    btn_removed.append(btn_list.pop(3))
                    del btn_list[3] # also remove separator that follows
                    total_width = get_total_width(btn_list)

            # draw buttons on the frame
            imgui.push_style_var(imgui.StyleVar_.item_spacing, (0, imgui.get_style().item_spacing.y))
            imgui.push_style_var(imgui.StyleVar_.frame_border_size, 0)
            open_popup = 0
            for i,b in enumerate(btn_list):
                id_str      = f'###path_comp_{i}'
                button_pos  = imgui.get_cursor_screen_pos()
                lbl = b[1]
                hover = None
                if isinstance(lbl, tuple):
                    hover = lbl[1]
                    lbl = lbl[0]
                if 'which' in self.path_bar_popup and \
                    self.path_bar_popup['which']==i-1 and \
                    imgui.is_popup_open('##dir_list_popup'):
                    lbl = 'v'
                if imgui.button(lbl+id_str):
                    if isinstance(b[0],str):
                        if i==0:
                            # if remote machines potentially available,
                            # enqueue opening machine selection dropdown
                            if self.file_action_provider.supports_remote() and self.allow_selecting_machine:
                                open_popup = 1
                                self.path_bar_popup['pos'] = button_pos
                                self.path_bar_popup['which_selected'] = self.machine
                        elif b[0]=='sep':
                            # path separator button, enqueue opening path selection dropdown
                            self.path_bar_popup['loc'] = self._get_parent(btn_list[i+1][0])
                            self.path_bar_popup['which'] = i-1
                            self.path_bar_popup['which_selected'] = btn_list[i+1][1]
                            self.path_bar_popup['pos'] = button_pos
                            open_popup = 2
                        elif b[0]=='ellipsis':
                            # draw dropdown with removed paths
                            self.path_bar_popup['loc'] = 'ellipsis'
                            self.path_bar_popup['which'] = None
                            self.path_bar_popup['pos'] = button_pos
                            open_popup = 2
                        else:
                            self.goto(self.machine, b[0])
                    else:
                        self.goto(self.machine, b[0])
                path_element_hc = path_element_hc or imgui.is_item_hovered() or imgui.is_item_clicked()
                if hover and imgui.is_item_hovered():
                    utils.draw_tooltip(hover)
                imgui.same_line()
            imgui.pop_style_var(2)
            imgui.pop_clip_rect()

            do_open_popup = False
            if open_popup==1:
                # machine selector
                imgui.open_popup('##machine_list_popup')
                do_open_popup = True
            elif open_popup==2:
                # directory selector
                if (isinstance(self.path_bar_popup['loc'],str) and self.path_bar_popup['loc']=='ellipsis') or \
                    (self.machine,self.path_bar_popup['loc']) in self._listing_cache:
                    imgui.open_popup('##dir_list_popup')
                    do_open_popup = True
            if do_open_popup:
                # position popup: move button height down so it pops under the button
                self.path_bar_popup['pos'].y += imgui.calc_text_size('x').y+2*imgui.get_style().frame_padding.y+imgui.get_style().item_spacing.y
                imgui.set_next_window_pos(self.path_bar_popup['pos'])
            if imgui.begin_popup('##machine_list_popup'):
                if self.show_local_machine:
                    display_names = [self.file_action_provider.local_name]
                else:
                    display_names = []
                display_names += list(self.file_action_provider.get_remotes().values())
                machines = [self.file_action_provider.get_full_machine_name(r) for r in display_names]
                if self.machine in machines:
                    idx = machines.index(self.machine)
                else:
                    idx = -1

                changed, idx = imgui.list_box('##machine_list_popup_select',idx,display_names)
                if changed:
                    self.goto(machines[idx], 'root')
                    imgui.close_current_popup()
                imgui.end_popup()
            if imgui.begin_popup('##dir_list_popup'):
                key = (self.machine,self.path_bar_popup['loc'])
                if key in self._listing_cache:
                    items = self._listing_cache[key]
                    items = [items[i] for i in items if items[i].is_dir]
                    paths         = [i.full_path for i in items]
                    display_names = [self._get_path_leaf_display_name(self.machine,p) for p in paths]
                    idx = display_names.index(self.path_bar_popup['which_selected'])
                elif isinstance(key[1],str) and key[1]=='ellipsis':
                    display_names = [b[1] for b in btn_removed]
                    paths = [b[0] for b in btn_removed]
                    display_names.reverse()
                    paths.reverse()
                    idx = -1
                changed, idx = imgui.list_box('##dir_list_popup_select',idx,display_names)
                if changed:
                    self.goto(self.machine, paths[idx])
                    imgui.close_current_popup()
                imgui.end_popup()


            # click state
            if not path_element_hc and clicked:
                state |= 0b001
                state &= 0b011      # remove SetKeyboardFocus flag
            else:
                state &= 0b110
            # hover state
            if not path_element_hc and hovered and not clicked:
                state |= 0b010
            else:
                state &= 0b101
            # allocate space
            imgui.set_cursor_pos(ui_pos)
            imgui.internal.item_size(bb)
        else:
            # input box
            skip_active_check = False
            if not state & 0b100:
                skip_active_check = True
                imgui.set_keyboard_focus_here()
                if not imgui.is_mouse_clicked(imgui.MouseButton_.left):
                    state |= 0b100

            loc_str = self._get_path_display_name(self.machine, self.loc, for_edit=True)
            confirmed, loc = imgui.input_text("##pathbox_input",loc_str,imgui.InputTextFlags_.enter_returns_true)
            if confirmed:
                self.goto(self.machine, loc)
            if not skip_active_check and not imgui.is_item_active():
                state &= 0b010

        win.state_storage.set_int(iid, state)

    def draw_bottom_bar(self):
        cancelled = closed = False

        # Cancel button
        if imgui.button(icons_fontawesome.ICON_FA_BAN+" Cancel"):
            imgui.close_current_popup()
            cancelled = closed = True
        # Ok button
        imgui.same_line()
        with self.items_lock:
            num_selected = sum([self.selected[iid] for iid in self.selected])
        disable_ok = not num_selected or (self.refreshing and self.new_loc)
        if disable_ok:
            imgui.internal.push_item_flag(imgui.internal.ItemFlags_.disabled, True)
            imgui.push_style_var(imgui.StyleVar_.alpha, imgui.get_style().alpha *  0.5)
        if imgui.button(icons_fontawesome.ICON_FA_CHECK+" Ok"):
            imgui.close_current_popup()
            closed = True
        if disable_ok:
            imgui.internal.pop_item_flag()
            imgui.pop_style_var()
        # Selected text
        imgui.same_line()
        imgui.text(f"  Selected {num_selected} items")

        return cancelled, closed

    def draw_listing(self, leave_space_for_bottom_bar: bool):
        closed = False
        size = imgui.ImVec2(imgui.get_item_rect_size().x, 0)
        if leave_space_for_bottom_bar:
            button_text_size = imgui.calc_text_size(icons_fontawesome.ICON_FA_BAN+" Cancel")
            bottom_margin = button_text_size.y+imgui.get_style().frame_padding.y*2+imgui.get_style().item_spacing.y
            size.y = -bottom_margin
        imgui.begin_child("##folder_contents", size=size)
        if self.refreshing and self.new_loc:
            string = 'loading directory...'
            t_size = imgui.calc_text_size(string)
            symbol_size = imgui.calc_text_size("x").y
            spinner_radii = [x/22*symbol_size for x in [22, 16, 10]]
            lw = 3.5/22*symbol_size
            tot_height = t_size.y+2*spinner_radii[0]+lw
            imgui.set_cursor_pos(((imgui.get_content_region_avail().x - t_size.x)/2, (imgui.get_content_region_avail().y - tot_height)/2))
            imgui.text(string)
            imgui.set_cursor_pos_x((imgui.get_content_region_avail().x - 2*spinner_radii[0]+lw)/2)
            imspinner.spinner_ang_triple(f'loadingSpinner', *spinner_radii, lw, c1=imgui.get_style().color_(imgui.Col_.text_selected_bg), c2=imgui.get_style().color_(imgui.Col_.text), c3=imgui.get_style().color_(imgui.Col_.text_selected_bg))
        elif self.msg:
            imgui.text_wrapped(self.msg)
        else:
            # do we have context menus?
            # not for special paths (which are strings) and not for share overviews of a network computer
            net_comps = file_actions.get_net_computer(self.loc)
            has_context_menu = not isinstance(self.loc, str) and (not net_comps or len(net_comps)>1)

            table_flags = (
                imgui.TableFlags_.scroll_x |
                imgui.TableFlags_.scroll_y |
                imgui.TableFlags_.hideable |
                imgui.TableFlags_.sortable |
                imgui.TableFlags_.sort_multi |
                imgui.TableFlags_.reorderable |
                imgui.TableFlags_.sizing_fixed_fit |
                imgui.TableFlags_.no_host_extend_y
            )
            if imgui.begin_table(f"##folder_list",column=5+self.allow_multiple,flags=table_flags):
                frame_height = imgui.get_frame_height()

                # Setup
                checkbox_width = frame_height-2*imgui.get_style().frame_padding.y
                if self.allow_multiple:
                    imgui.table_setup_column("Selector", imgui.TableColumnFlags_.no_hide | imgui.TableColumnFlags_.no_sort | imgui.TableColumnFlags_.no_resize | imgui.TableColumnFlags_.no_reorder, init_width_or_weight=checkbox_width)  # 0
                imgui.table_setup_column("Name", imgui.TableColumnFlags_.width_stretch | imgui.TableColumnFlags_.default_sort | imgui.TableColumnFlags_.no_hide)  # 1
                imgui.table_setup_column("Date created", imgui.TableColumnFlags_.default_hide)  # 2
                imgui.table_setup_column("Date modified")  # 3
                imgui.table_setup_column("Type")  # 4
                imgui.table_setup_column("Size")  # 5
                imgui.table_setup_scroll_freeze(int(self.allow_multiple), 1)  # Sticky column headers and selector row

                with self.items_lock:
                    sort_specs = imgui.table_get_sort_specs()
                    self.sort_items(sort_specs)

                    # Headers
                    imgui.table_next_row(imgui.TableRowFlags_.headers)
                    # checkbox column: reflects whether all, some or none of visible recordings are selected, and allows selecting all or none
                    num_selected = sum([self.selected[iid] for iid in self.selected])
                    if self.allow_multiple:
                        imgui.table_set_column_index(0)
                        # determine state
                        if self.predicate:
                            num_items = sum([self.predicate(iid) for iid in self.items])
                        else:
                            num_items = len(self.items)
                        if num_selected==0:
                            # none selected
                            multi_selected_state = -1
                        elif num_selected==num_items:
                            # all selected
                            multi_selected_state = 1
                        else:
                            # some selected
                            multi_selected_state = 0

                        if multi_selected_state==0:
                            imgui.internal.push_item_flag(imgui.internal.ItemFlags_.mixed_value, True)
                        clicked, new_state = utils.my_checkbox(f"##header_checkbox", multi_selected_state==1, frame_size=(0,0), frame_padding_override=(imgui.get_style().frame_padding.x/2,0), do_vertical_align=False)
                        if multi_selected_state==0:
                            imgui.internal.pop_item_flag()

                        if clicked:
                            utils.set_all(self.selected, new_state, subset = self.sorted_items, predicate=self.predicate)

                    for i in range(5):
                        imgui.table_set_column_index(i+self.allow_multiple)
                        imgui.table_header(imgui.table_get_column_name(i+self.allow_multiple))


                    # Loop rows
                    any_selectable_clicked = False
                    new_loc = None
                    if self.sorted_items and self.last_clicked_id not in self.sorted_items:
                        # default to topmost if last_clicked unknown, or no longer on screen due to filter
                        self.last_clicked_id = self.sorted_items[0]
                    clipper = imgui.ListClipper()
                    clipper.begin(len(self.sorted_items))
                    while clipper.step():
                        for iid in self.sorted_items[clipper.display_start:clipper.display_end]:
                            imgui.table_next_row()

                            selectable_clicked = False
                            checkbox_clicked, checkbox_hovered, checkbox_out = False, False, False
                            has_drawn_hitbox = False

                            disable_item = self.predicate and not self.predicate(iid)
                            if disable_item:
                                imgui.internal.push_item_flag(imgui.internal.ItemFlags_.disabled, True)
                                imgui.push_style_var(imgui.StyleVar_.alpha, imgui.get_style().alpha * 0.5)

                            for ci in range(5+self.allow_multiple):
                                if not (imgui.table_get_column_flags(ci) & imgui.TableColumnFlags_.is_enabled):
                                    continue
                                imgui.table_set_column_index(ci)

                                # Row hitbox
                                if not has_drawn_hitbox:
                                    # hitbox needs to be drawn before anything else on the row so that, together with imgui.set_item_allow_overlap(), hovering button
                                    # or checkbox on the row will still be correctly detected.
                                    # this is super finicky, but works. The below together with using a height of frame_height+cell_padding_y
                                    # makes the table row only cell_padding_y/2 longer. The whole row is highlighted correctly
                                    cell_padding_y = imgui.get_style().cell_padding.y
                                    cur_pos_y = imgui.get_cursor_pos_y()
                                    imgui.set_cursor_pos_y(cur_pos_y - cell_padding_y/2)
                                    imgui.push_style_var(imgui.StyleVar_.frame_border_size, 0.)
                                    imgui.push_style_var(imgui.StyleVar_.frame_padding    , (0.,0.))
                                    imgui.push_style_var(imgui.StyleVar_.item_spacing     , (0.,cell_padding_y))
                                    selectable_clicked, selectable_out = imgui.selectable(f"##{iid}_hitbox", self.selected[iid], flags=imgui.SelectableFlags_.span_all_columns|imgui.SelectableFlags_.allow_overlap|imgui.internal.SelectableFlagsPrivate_.select_on_click, size=(0,frame_height+cell_padding_y))
                                    imgui.set_cursor_pos_y(cur_pos_y)   # instead of imgui.same_line(), we just need this part of its effect
                                    imgui.pop_style_var(3)
                                    selectable_right_clicked = utils.handle_item_hitbox_events(iid, self.selected, context_menu=lambda _: self._item_context_menu([iid for iid in self.sorted_items if self.selected[iid]]) if has_context_menu else None)
                                    has_drawn_hitbox = True

                                if ci==int(self.allow_multiple):
                                    # (Invisible) button because it aligns the following draw calls to center vertically
                                    imgui.push_style_var(imgui.StyleVar_.frame_border_size, 0.)
                                    imgui.push_style_var(imgui.StyleVar_.frame_padding    , (0.,imgui.get_style().frame_padding.y))
                                    imgui.push_style_var(imgui.StyleVar_.item_spacing     , (0.,imgui.get_style().item_spacing.y))
                                    imgui.push_style_color(imgui.Col_.button, (0.,0.,0.,0.))
                                    imgui.button(f"##{iid}_id", size=(imgui.FLT_MIN,0))
                                    imgui.pop_style_color()
                                    imgui.pop_style_var(3)

                                    imgui.same_line()

                                match ci+int(not self.allow_multiple):
                                    case 0:
                                        # Selector
                                        checkbox_clicked, checkbox_out = utils.my_checkbox(f"##{iid}_selected", self.selected[iid], frame_size=(0,0), frame_padding_override=(imgui.get_style().frame_padding.x/2,imgui.get_style().frame_padding.y))
                                        checkbox_hovered = imgui.is_item_hovered()
                                    case 1:
                                        # Name
                                        imgui.set_cursor_pos_x(imgui.get_cursor_pos_x() - imgui.calc_text_size(DIR_ICON).x/3)
                                        imgui.text(self.items[iid].display_name)
                                    case 2:
                                        # Date created
                                        if self.items[iid].ctime_str:
                                            imgui.text(self.items[iid].ctime_str)
                                    case 3:
                                        # Date modified
                                        if self.items[iid].mtime_str:
                                            imgui.text(self.items[iid].mtime_str)
                                    case 4:
                                        # Type
                                        if self.items[iid].mime_type:
                                            disp_str = utils.trim_str(self.items[iid].mime_type,20)
                                            imgui.text(disp_str)
                                            if disp_str!=self.items[iid].mime_type and imgui.is_item_hovered():
                                                utils.draw_hover_text(self.items[iid].mime_type, text='')
                                    case 5:
                                        # Size
                                        if self.items[iid].size_str:
                                            imgui.text(self.items[iid].size_str)

                            if disable_item:
                                imgui.internal.pop_item_flag()
                                imgui.pop_style_var()

                            # handle selection logic
                            # NB: the part of this logic that has to do with right-clicks is in handle_item_hitbox_events()
                            # NB: any_selectable_clicked is just for handling clicks not on any item
                            any_selectable_clicked = any_selectable_clicked or selectable_clicked or selectable_right_clicked
                            self.last_clicked_id = utils.selectable_item_logic(
                                iid, self.selected, self.last_clicked_id, self.sorted_items,
                                selectable_clicked, selectable_out, allow_multiple=self.allow_multiple,
                                overlayed_hovered=checkbox_hovered, overlayed_clicked=checkbox_clicked, new_overlayed_state=checkbox_out
                                )

                            # further deal with doubleclick on item
                            if selectable_clicked and not checkbox_hovered: # don't enter this branch if interaction is with checkbox on the table row
                                if not imgui.get_io().key_ctrl and not imgui.get_io().key_shift and imgui.is_mouse_double_clicked(imgui.MouseButton_.left):
                                    if self.items[iid].is_dir:
                                        new_loc = self.items[iid].full_path
                                        break
                                    else:
                                        utils.set_all(self.selected, False)
                                        self.selected[iid] = True
                                        imgui.close_current_popup()
                                        closed = True

                    # handle action keys
                    if not self.popup_stack and not self.disable_keyboard_navigation and not imgui.get_io().want_text_input:
                        # no keyboard navigation in this GUI if a popup is open or key input taken elsewhere
                        selected_ids = [iid for iid in self.sorted_items if self.selected[iid]]
                        if len(selected_ids)==1:
                            if imgui.is_key_pressed(imgui.Key.f2, repeat=False):
                                self.dialog_provider.show_rename_path_dialog(self.items[selected_ids[0]].full_path)
                            if self.items[selected_ids[0]].is_dir and imgui.is_key_pressed(imgui.Key.enter, repeat=False):
                                new_loc = self.items[selected_ids[0]].full_path
                        if selected_ids and imgui.is_key_pressed(imgui.Key.delete, repeat=False):
                            self.dialog_provider.show_delete_path_dialog(self.items, selected_ids)

                if new_loc:
                    self.goto(self.machine, new_loc)
                last_y = imgui.get_cursor_screen_pos().y
                imgui.end_table()

                # handle click in table area outside header+contents:
                # deselect all, and if right click, show popup
                # check mouse is below bottom of last drawn row so that clicking on the one pixel empty space between selectables
                # does not cause everything to unselect or popup to open
                if not any_selectable_clicked and imgui.get_io().mouse_pos.y>last_y:
                    if imgui.is_item_clicked(imgui.MouseButton_.left):  # left mouse click (NB: table header is not signalled by is_item_clicked(), so this works correctly)
                        utils.set_all(self.selected, False)
                    # show menu when right-clicking the empty space
                    if imgui.is_item_clicked(imgui.MouseButton_.right): # NB: mouse down
                        utils.set_all(self.selected, False)  # deselect on right mouse click as well
                    if has_context_menu and imgui.begin_popup_context_item("##file_list_context"):   # NB: mouse up
                        if imgui.selectable(f"New folder##button", False)[0]:
                            self.dialog_provider.show_new_folder_dialog(self.loc)
                        imgui.end_popup()

        imgui.end_child()
        return closed

    def _item_context_menu(self, iids: list[int]):
        disabled = len(iids)!=1
        if disabled:
            utils.push_disabled()
        if imgui.selectable(f"Rename##button", False)[0]:
            self.dialog_provider.show_rename_path_dialog(self.items[iids[0]].full_path)
        if disabled:
            utils.pop_disabled()
        if imgui.selectable(f"Delete##button", False)[0]:
            self.dialog_provider.show_delete_path_dialog(self.items, iids)
        if imgui.selectable(f"New folder here##button", False)[0]:
            self.dialog_provider.show_new_folder_dialog(self.items[iids[0]].full_path.parent)

    def tick(self):
        # Auto refresh
        if not self.refreshing and (self.elapsed>2 or imgui.is_key_pressed(imgui.Key.f5)):
            self.refresh()

        # Setup popup
        if not imgui.is_popup_open(self.title):
            imgui.open_popup(self.title)
        opened = 1
        size = imgui.get_io().display_size
        size.x *= .7
        size.y *= .7
        imgui.set_next_window_size(size, cond=imgui.Cond_.appearing)
        if imgui.begin_popup_modal(self.title, True, flags=self.default_flags)[0]:
            cancelled = closed = utils.close_weak_popup(check_click_outside=False)
            cancelled2, closed2 = self.draw()
            cancelled = cancelled or cancelled2
            closed    = closed    or closed2
        else:
            opened = 0
            cancelled = closed = True
        if closed:
            if not cancelled and self.callback:
                with self.items_lock:
                    selected = [self.items[iid].full_path for iid in self.items if iid in self.selected and self.selected[iid]]
                self.callback(selected if selected else None)

        utils.handle_popup_stack(self.popup_stack)
        return opened, closed

    def sort_items(self, sort_specs_in: imgui.TableSortSpecs):
        if sort_specs_in.specs_dirty or self.require_sort:
            ids = list(self.items)
            sort_specs = [sort_specs_in.get_specs(i) for i in range(sort_specs_in.specs_count)]
            for sort_spec in reversed(sort_specs):
                match sort_spec.column_index+int(not self.allow_multiple):
                    case 2:     # Date created
                        key = lambda iid: self.items[iid].ctime
                    case 3:     # Date modified
                        key = lambda iid: self.items[iid].mtime
                    case 4:     # Type
                        key = lambda iid: m if (m:=self.items[iid].mime_type) else ''
                    case 5:     # Size
                        key = lambda iid: self.items[iid].size

                    case _:     # Name and all others
                        key = natsort.os_sort_keygen(key=lambda iid: self.items[iid].full_path)

                ids.sort(key=key, reverse=bool(sort_spec.get_sort_direction() - 1))

            # finally, always sort dirs first
            ids.sort(key=lambda iid: self.items[iid].is_dir, reverse=True)
            self.sorted_items = ids

            # apply filter, if any
            if self.filter_box_text:
                search = self.filter_box_text.casefold()
                def key(iid):
                    item = self.items[iid]
                    return search in item.display_name.casefold()
                self.sorted_items = list(filter(key, self.sorted_items))

            # we're done
            sort_specs_in.specs_dirty = False
            self.require_sort = False

class DialogProvider:
    def __init__(self, gui, action_provider):
        self.gui = gui
        self.action_provider = action_provider
    def show_new_folder_dialog(self, parent=pathlib.Path):
        new_folder_name = 'New folder'
        width = imgui.calc_text_size('x').x*35
        setup_done = False
        def _new_folder_popup():
            nonlocal new_folder_name, setup_done
            if imgui.begin_table("##new_folder",2):
                imgui.table_setup_column("##new_folder_left", imgui.TableColumnFlags_.width_fixed)
                imgui.table_setup_column("##new_folder_right", imgui.TableColumnFlags_.width_stretch)
                imgui.table_next_row()
                imgui.table_next_column()
                imgui.align_text_to_frame_padding()
                imgui.text("Folder name")
                imgui.table_next_column()
                imgui.set_next_item_width(width)
                if not setup_done:
                    imgui.set_keyboard_focus_here()
                    setup_done = True
                _,new_folder_name = imgui.input_text("##new_folder_name", new_folder_name)
                imgui.end_table()
            return 0 if imgui.is_key_released(imgui.Key.enter) else None
        buttons = {
            icons_fontawesome.ICON_FA_CHECK+" Make folder": lambda: self.action_provider('make_dir',parent/new_folder_name),
            icons_fontawesome.ICON_FA_BAN+" Cancel": None
        }
        utils.push_popup(self.gui, lambda: utils.popup("Make folder", _new_folder_popup, buttons = buttons, closable=True))

    def show_rename_path_dialog(self, item: pathlib.Path):
        item_name = item.name
        width = imgui.calc_text_size('x').x*(len(item_name)+15)
        setup_done = False
        def _rename_item_popup():
            nonlocal item_name, setup_done
            if imgui.begin_table("##rename_item",2):
                imgui.table_setup_column("##rename_item_left", imgui.TableColumnFlags_.width_fixed)
                imgui.table_setup_column("##rename_item_right", imgui.TableColumnFlags_.width_stretch)
                imgui.table_next_row()
                imgui.table_next_column()
                imgui.align_text_to_frame_padding()
                imgui.text("Item name")
                imgui.table_next_column()
                imgui.set_next_item_width(width)
                if not setup_done:
                    imgui.set_keyboard_focus_here()
                    setup_done = True
                _,item_name = imgui.input_text("##new_rename_item", item_name)
                imgui.end_table()
            return 0 if imgui.is_key_released(imgui.Key.enter) else None
        buttons = {
            icons_fontawesome.ICON_FA_CHECK+" Rename": lambda: self.action_provider('rename_path', item, item.parent / item_name),
            icons_fontawesome.ICON_FA_BAN+" Cancel": None
        }
        utils.push_popup(self.gui, lambda: utils.popup("Rename item", _rename_item_popup, buttons = buttons, closable=True))

    def show_delete_path_dialog(self, file_picker_items, iids: list[int]):
        # NB: assume items lock acquired when this is called
        paths = [file_picker_items[iid].full_path for iid in iids]
        paths_disp = '\n '.join([file_picker_items[iid].display_name for iid in iids])
        def _delete_item_popup():
            imgui.text(f'Are you sure you want to delete \n {paths_disp}')
            return 0 if imgui.is_key_released(imgui.Key.enter) else None
        def _launch_deletes():
            for p in paths:
                self.action_provider('delete_path', p)
        buttons = {
            icons_fontawesome.ICON_FA_TRASH+" Delete": _launch_deletes,
            icons_fontawesome.ICON_FA_BAN+" Cancel": None
        }
        utils.push_popup(self.gui, lambda: utils.popup("Delete item", _delete_item_popup, buttons = buttons, closable=True))