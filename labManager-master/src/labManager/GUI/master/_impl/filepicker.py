import pathlib
import typing
from imgui_bundle import imgui, icons_fontawesome, imspinner
import sys
import natsort
import re
import asyncio
import concurrent
import threading
from dataclasses import dataclass
from typing import Callable

from labManager.common import async_thread, dir_list, structs
from . import utils


DRIVE_ICON = icons_fontawesome.ICON_FA_HDD
SERVER_ICON = icons_fontawesome.ICON_FA_SERVER
DIR_ICON = icons_fontawesome.ICON_FA_FOLDER
FILE_ICON = icons_fontawesome.ICON_FA_FILE


@dataclass
class DirEntryWithCache(structs.DirEntry):
    # display fields
    display_name: str
    ctime_str: str
    mtime_str: str
    size_str: str

    def __init__(self, item: structs.DirEntry):
        super().__init__(item.name, item.is_dir, item.full_path, item.ctime, item.mtime, item.size, item.mime_type)

        # prep display strings
        if self.mime_type=='labManager/drive':
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
        if not self.is_dir:
            unit = 1024**2
            orig = "%.1f KiB" % ((1024 * self.size / unit))
            while True:
                # add commas as thousands separators, if any are needed
                new = re.sub(r"^(-?\d+)(\d{3})", r"\g<1>,\g<2>", orig)
                if orig == new:
                    break
                orig = new
            self.size_str = new
        else:
            self.size_str = None


class DirectoryProvider:
    def __init__(self, drive_callback: Callable[[list[str]], None], listing_callback: Callable[[list[structs.DirEntry]|Exception], None]):
        self.waiters: set[asyncio.Future] = set()

        self.drive_callback: Callable[[list[str]], None] = drive_callback
        self.listing_callback: Callable[[list[structs.DirEntry]|Exception], None] = listing_callback

    def __del__(self):
        pass

    def get_listing(self, path: str|pathlib.Path) -> tuple[list[structs.DirEntry],asyncio.Future]:
        if path=='root':
            fut = self._get_drives('listing')
        else:
            fut = async_thread.run(dir_list.get_dir_list(path), lambda f: self.action_done(f, 'listing'))
            self.waiters.add(fut)
        return [],fut   # can return any cache we may have. Understood to be potentially stale

    def get_drives(self) -> tuple[list[str],asyncio.Future]:
        fut = self._get_drives('drives')
        return [],fut   # can return any cache we may have. Understood to be potentially stale

    def _get_drives(self, action_type: str) -> asyncio.Future:
        fut = async_thread.run(dir_list.get_drives(), lambda f: self.action_done(f, action_type))
        self.waiters.add(fut)
        return fut

    def action_done(self, fut: asyncio.Future, which: str):
        try:
            result = fut.result()
        except concurrent.futures.CancelledError:
            return  # nothing more to do
        except Exception as exc:
            result = exc

        if result is not None:
            match which:
                case 'drives':
                    cb = self.drive_callback
                case 'listing':
                    cb = self.listing_callback
        if cb:
            cb(result)
        self.waiters.discard(fut)


class FilePicker:
    default_flags: int = (
        imgui.WindowFlags_.no_collapse |
        imgui.WindowFlags_.no_saved_settings
    )

    def __init__(self, title="File picker", start_dir: str | pathlib.Path = None, callback: typing.Callable = None, allow_multiple = True, directory_service=None, custom_popup_flags=0):
        self.title = title
        self.elapsed = 0.0
        self.callback = callback
        self.directory_service = directory_service
        if self.directory_service is None:
            self.directory_service = DirectoryProvider(self._refresh_drives_done, self._refresh_path_done)

        self.items: dict[int, DirEntryWithCache] = {}
        self.items_lock: threading.Lock = threading.Lock()
        self.selected: dict[int, bool] = {}
        self.allow_multiple = allow_multiple
        self.msg: str = None
        self.require_sort = False
        self.sorted_items: list[int] = []
        self.last_clicked_id: int = None

        self.loc: pathlib.Path = None
        self.refreshing = False
        self.drive_refresh_task: asyncio.Future = None
        self.listing_refresh_task: asyncio.Future = None
        self.new_loc = False
        self.predicate = None
        self.default_flags = custom_popup_flags or FilePicker.default_flags
        self.platform_is_windows = sys.platform.startswith("win")
        # only relevant on Windows
        self.drives_lock: threading.Lock = threading.Lock()
        self.drives: list[DirEntryWithCache] = []
        self.current_drive = -1

        self.goto(start_dir or '.')

    # if passed a single directory will show that directory
    # if passed a single file, or multiple files and/or directories, will
    # open the parent of those and select them (kinda like "show in folder")
    def set_dir(self, paths: pathlib.Path | list[pathlib.Path]):
        if not isinstance(paths,list):
            paths = [paths]
        paths = [pathlib.Path(p) for p in paths]

        if len(paths)==1 and paths[0].is_dir():
            self.goto(paths[0])
        else:
            self.goto(paths[0].parent)
            # select dropped items that match predicate (if any)
            self._select_paths(paths)

    def goto(self, loc: str | pathlib.Path):
        if isinstance(loc, str):
            if loc.casefold=='my computer':
                loc = 'root'
            is_root = loc=='root'
        else:
            is_root = False
        if not is_root:
            loc = pathlib.Path(loc)
            if loc.is_file():
                loc = loc.parent
            if loc is None:
                loc = pathlib.Path('.')
            loc = loc.resolve()

        if loc != self.loc:
            self.loc = loc
            self.new_loc = True
            # changing location clears selection
            utils.set_all(self.selected, False)
            # load new directory
            self._set_current_drive()   # update the drive selector already
            self.refresh()

    def refresh(self):
        # if there is an ongoing refresh, cancel it
        if self.listing_refresh_task:
            self.listing_refresh_task.cancel()
        if self.drive_refresh_task:
            self.drive_refresh_task.cancel()
        # launch refresh
        self.refreshing = True
        cache, self.listing_refresh_task = self.directory_service.get_listing(self.loc)
        cache, self.drive_refresh_task   = self.directory_service.get_drives()

    def _refresh_path_done(self, items: list[structs.DirEntry]|Exception):
        previously_selected = []
        with self.items_lock:
            if not self.new_loc:
                previously_selected = [self.items[iid].full_path for iid in self.items if iid in self.selected and self.selected[iid]]
            self.items.clear()
            self.selected.clear()
            self.msg = None
            if isinstance(items, Exception):
                self.msg = f"Cannot open this folder!\n:{items}"
            else:
                self.items = {i:DirEntryWithCache(item) for i,item in enumerate(items)}
                self.selected = {k:False for k in self.items}
                if not self.items:
                    self.msg = "This folder is empty!"

        # if refreshed the same directory, restore old selection
        self._select_paths(previously_selected)

        self.require_sort = True
        self.new_loc = False
        self.refreshing = False
        self.elapsed = 0.0

    def _refresh_drives_done(self, drives: list[structs.DirEntry]|Exception):
        # refresh drives and set up directory selector
        if isinstance(drives, Exception):
            return
        with self.drives_lock:
            self.drives = [DirEntryWithCache(item) for item in drives]
        self._set_current_drive()

    def _set_current_drive(self):
        with self.drives_lock:
            for i,d in enumerate(self.drives):
                if str(self.loc).startswith(d.name):
                    self.current_drive = i
                    break

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

    def tick(self):
        # Auto refresh
        self.elapsed += imgui.get_io().delta_time
        if self.elapsed > 2 and not self.refreshing:
            self.refresh()

        # Setup popup
        if not imgui.is_popup_open(self.title):
            imgui.open_popup(self.title)
        cancelled = closed = False
        opened = 1
        size = imgui.get_io().display_size
        size.x *= .7
        size.y *= .7
        imgui.set_next_window_size(size, cond=imgui.Cond_.appearing)
        if imgui.begin_popup_modal(self.title, True, flags=self.default_flags)[0]:
            cancelled = closed = utils.close_weak_popup(check_click_outside=False)
            imgui.begin_group()
            # Up button
            if imgui.button(icons_fontawesome.ICON_FA_ARROW_UP):
                parent = self.loc.parent
                if parent==self.loc:
                    # we're in a drive root, one higher would be the drive list (denoted by root)
                    parent = 'root'
                self.goto(parent)
            # Refresh button
            imgui.same_line()
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
                imspinner.spinner_ang_triple(f'loginSpinner', *spinner_radii, lw, c1=imgui.get_style().color_(imgui.Col_.text_selected_bg), c2=imgui.get_style().color_(imgui.Col_.text), c3=imgui.get_style().color_(imgui.Col_.text_selected_bg))
                imgui.set_cursor_pos(cp)
                imgui.dummy(button_size)
                utils.draw_hover_text(text='', hover_text='Refreshing...')
            else:
                if imgui.button(icons_fontawesome.ICON_FA_REDO):
                    self.refresh()
            # Drive selector
            is_root = self.loc=='root'
            if self.drives:
                imgui.same_line()
                imgui.set_next_item_width(imgui.get_font_size() * 4)
                with self.drives_lock:
                    if is_root:
                        drive_list = [('','')]
                        current_drive = 0
                    else:
                        drive_list = []
                        current_drive = self.current_drive
                    drive_list.extend([(d.display_name,d.full_path) for d in self.drives])

                changed, value = imgui.combo("##drive_selector", current_drive, [d[0] for d in drive_list])
                if changed:
                    self.goto(drive_list[value][1])
            # Location bar
            imgui.same_line()
            imgui.set_next_item_width(imgui.get_content_region_avail().x)
            if is_root:
                loc_str = 'My Computer'
            else:
                loc_str = str(self.loc)
            confirmed, loc = imgui.input_text("##location_bar", loc_str, flags=imgui.InputTextFlags_.enter_returns_true)
            if imgui.begin_popup_context_item(f"##location_context"):
                if imgui.selectable(icons_fontawesome.ICON_FA_PASTE+" Paste", False)[0] and (loc := imgui.get_clipboard_text()):
                    confirmed = True
                imgui.end_popup()
            if confirmed:
                self.goto(loc)
            imgui.end_group()

            # entry list
            num_selected = 0
            button_text_size = imgui.calc_text_size(icons_fontawesome.ICON_FA_BAN+" Cancel")
            bottom_margin = button_text_size.y+imgui.get_style().frame_padding.y*2+imgui.get_style().item_spacing.y
            imgui.begin_child("##folder_contents", size=(imgui.get_item_rect_size().x, -bottom_margin))
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
                imspinner.spinner_ang_triple(f'loginSpinner', *spinner_radii, lw, c1=imgui.get_style().color_(imgui.Col_.text_selected_bg), c2=imgui.get_style().color_(imgui.Col_.text), c3=imgui.get_style().color_(imgui.Col_.text_selected_bg))
            elif self.msg:
                imgui.text_unformatted(self.msg)
            else:
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
                    checkbox_width = frame_height
                    if self.allow_multiple:
                        imgui.table_setup_column("Selector", imgui.TableColumnFlags_.no_hide | imgui.TableColumnFlags_.no_sort | imgui.TableColumnFlags_.no_resize | imgui.TableColumnFlags_.no_reorder, init_width_or_weight=checkbox_width)  # 0
                    imgui.table_setup_column("Name", imgui.TableColumnFlags_.width_stretch | imgui.TableColumnFlags_.default_sort | imgui.TableColumnFlags_.no_hide)  # 1
                    imgui.table_setup_column("Date created", imgui.TableColumnFlags_.default_hide)  # 2
                    imgui.table_setup_column("Date modified")  # 3
                    imgui.table_setup_column("Type")  # 4
                    imgui.table_setup_column("Size")  # 5
                    imgui.table_setup_scroll_freeze(int(self.allow_multiple), 1)  # Sticky column headers and selector row

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
                            with self.items_lock:
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
                        clicked, new_state = utils.my_checkbox(f"##header_checkbox", multi_selected_state==1, frame_size=(0,0), do_vertical_align=False)
                        if multi_selected_state==0:
                            imgui.internal.pop_item_flag()

                        if clicked:
                            utils.set_all(self.selected, new_state, subset = self.sorted_items, predicate=self.predicate)

                    for i in range(5):
                        imgui.table_set_column_index(i+self.allow_multiple)
                        imgui.table_header(imgui.table_get_column_name(i+self.allow_multiple))


                    # Loop rows
                    any_selectable_clicked = False
                    with self.items_lock:
                        if self.sorted_items and self.last_clicked_id not in self.sorted_items:
                            # default to topmost if last_clicked unknown, or no longer on screen due to filter
                            self.last_clicked_id = self.sorted_items[0]
                        for iid in self.sorted_items:
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
                                        checkbox_clicked, checkbox_out = utils.my_checkbox(f"##{iid}_selected", self.selected[iid], frame_size=(0,0))
                                        checkbox_hovered = imgui.is_item_hovered()
                                    case 1:
                                        # Name
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
                                            imgui.text(self.items[iid].mime_type)
                                    case 5:
                                        # Size
                                        if self.items[iid].size_str:
                                            imgui.text(self.items[iid].size_str)

                            if disable_item:
                                imgui.internal.pop_item_flag()
                                imgui.pop_style_var()

                            # handle selection logic
                            # NB: any_selectable_clicked is just for handling clicks not on any item
                            any_selectable_clicked = any_selectable_clicked or selectable_clicked
                            self.last_clicked_id = utils.selectable_item_logic(
                                iid, self.selected, self.last_clicked_id, self.sorted_items,
                                selectable_clicked, selectable_out, allow_multiple=self.allow_multiple,
                                overlayed_hovered=checkbox_hovered, overlayed_clicked=checkbox_clicked, new_overlayed_state=checkbox_out
                                )

                            # further deal with doubleclick on item
                            if selectable_clicked and not checkbox_hovered: # don't enter this branch if interaction is with checkbox on the table row
                                if not imgui.get_io().key_ctrl and not imgui.get_io().key_shift and imgui.is_mouse_double_clicked(imgui.MouseButton_.left):
                                    if self.items[iid].is_dir:
                                        self.goto(self.items[iid].full_path)
                                        break
                                    else:
                                        utils.set_all(self.selected, False)
                                        self.selected[iid] = True
                                        imgui.close_current_popup()
                                        closed = True

                    last_y = imgui.get_cursor_screen_pos().y
                    imgui.end_table()

                    # handle click in table area outside header+contents:
                    # deselect all, and if right click, show popup
                    # check mouse is below bottom of last drawn row so that clicking on the one pixel empty space between selectables
                    # does not cause everything to unselect or popup to open
                    if imgui.is_item_clicked() and not any_selectable_clicked and imgui.get_io().mouse_pos.y>last_y:  # left mouse click (NB: table header is not signalled by is_item_clicked(), so this works correctly)
                        utils.set_all(self.selected, False)

            imgui.end_child()

            # Cancel button
            if imgui.button(icons_fontawesome.ICON_FA_BAN+" Cancel"):
                imgui.close_current_popup()
                cancelled = closed = True
            # Ok button
            imgui.same_line()
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
        else:
            opened = 0
            cancelled = closed = True
        if closed:
            if not cancelled and self.callback:
                selected = [self.items[iid].full_path for iid in self.items if iid in self.selected and self.selected[iid]]
                self.callback(selected if selected else None)
        return opened, closed

    def sort_items(self, sort_specs_in: imgui.TableSortSpecs):
        if sort_specs_in.specs_dirty or self.require_sort:
            with self.items_lock:
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
                sort_specs_in.specs_dirty = False
                self.require_sort = False
