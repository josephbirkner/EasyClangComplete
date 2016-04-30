import re
import subprocess
import importlib
import sublime
import time

from os import path
from os import listdir

from .tools import PKG_NAME

cindex_dict = {
    '3.2': PKG_NAME + ".clang.cindex32",
    '3.3': PKG_NAME + ".clang.cindex33",
    '3.4': PKG_NAME + ".clang.cindex34",
    '3.5': PKG_NAME + ".clang.cindex35",
    '3.6': PKG_NAME + ".clang.cindex36",
    '3.7': PKG_NAME + ".clang.cindex37",
    '3.8': PKG_NAME + ".clang.cindex38",
}


class CompleteHelper:
    """docstring for CompleteHelper"""

    tu_module = None
    version_str = None

    completions = []
    translation_units = {}
    async_completions_ready = False

    def __init__(self, clang_binary, verbose):
        """Initialize the CompleteHelper

        Args:
            view (sublime.View): Description
        """
        # check if clang binary is defined
        if not clang_binary:
            if verbose:
                print(PKG_NAME + ": clang binary not defined")
            return

        # run the cmd to get the proper version of the installed clang
        check_version_cmd = clang_binary + " --version"
        try:
            output = subprocess.check_output(check_version_cmd, shell=True)
            output_text = ''.join(map(chr, output))
        except subprocess.CalledProcessError as e:
            print(PKG_NAME + ": {}".format(e))
            print(PKG_NAME + ": ERROR: make sure '{}' is in PATH."
                  .format(clang_binary))
            return

        # now we have the output, and can extract version from it
        version_regex = re.compile("\d.\d")
        found = version_regex.search(output_text)
        CompleteHelper.version_str = found.group()
        if CompleteHelper.version_str > "3.8":
            CompleteHelper.version_str = "3.8"
        if verbose:
            print(PKG_NAME + ": found a cindex for clang v: "
                  + CompleteHelper.version_str)
        if CompleteHelper.version_str in cindex_dict:
            try:
                # should work if python bindings are installed
                cindex = importlib.import_module("clang.cindex")
            except Exception as e:
                # should work for other cases
                if verbose:
                    print("{}: cannot get default clang with error: {}".format(
                        PKG_NAME, e))
                    print("{}: getting bundled one: {}".format(
                        PKG_NAME, cindex_dict[CompleteHelper.version_str]))
                cindex = importlib.import_module(
                    cindex_dict[CompleteHelper.version_str])
            CompleteHelper.tu_module = cindex.TranslationUnit

    def get_diagnostics(self, view_id):
        if view_id not in self.translation_units:
            # no tu for this view
            return None
        return self.translation_units[view_id].diagnostics

    def remove_tu(self, view_id, verbose):
        if view_id not in self.translation_units:
            # nothing to remove
            return
        del self.translation_units[view_id]

    def init_completer(self, view_id, initial_includes, search_include_file, std_flag,
                       file_name, file_body, project_base_folder, verbose):
        """Initialize the completer

        Args:
            view (sublime.View): Description
        """
        # initialize all includes
        all_includes = initial_includes

        file_current_folder = path.dirname(file_name)

        # support .clang_complete file with -I<indlude> entries
        if search_include_file:
            clang_complete_file = CompleteHelper._search_clang_complete_file(
                file_current_folder, project_base_folder)
            if clang_complete_file:
                if verbose:
                    print("{}: found {}".format(PKG_NAME, clang_complete_file))
                parsed_includes = CompleteHelper._parse_clang_complete_file(
                    clang_complete_file, verbose)
                all_includes += parsed_includes

        # initialize unsaved files
        files = [(file_name, file_body)]

        # init needed variables from settings
        clang_includes = []
        for include in all_includes:
            clang_includes.append("-I" + include)

        try:
            TU = CompleteHelper.tu_module
            if verbose:
                print(PKG_NAME + ": compilation started.")
            self.translation_units[view_id] = TU.from_source(
                file_name, [std_flag] + clang_includes,
                unsaved_files=files,
                options=TU.PARSE_PRECOMPILED_PREAMBLE |
                TU.PARSE_CACHE_COMPLETION_RESULTS)
            if verbose:
                print(PKG_NAME + ": compilation done.")
        except Exception as e:
            print(PKG_NAME + ":", e)

    def complete(self, view, cursor_pos):
        """This function is called asynchronously to create a list of
        autocompletions. Using the current translation unit it queries libclang
        about the possible completions.

        Args:
            view (sublime.View): current view
            cursor_pos (int): sublime provided poistion of the cursor

        """
        file_body = view.substr(sublime.Region(0, view.size()))
        (row, col) = view.rowcol(cursor_pos)
        row += 1
        col += 1

        # unsaved files
        files = [(view.file_name(), file_body)]

        # do nothing if there in no translation_unit present
        if not view.id() in self.translation_units:
            return None
        # execute clang code completion
        complete_results = self.translation_units[view.id()].codeComplete(
            view.file_name(),
            row, col,
            unsaved_files=files)
        if complete_results is None or len(complete_results.results) == 0:
            print("no completions")
            return None

        self.completions = CompleteHelper._process_completions(
            complete_results)
        self.async_completions_ready = True
        CompleteHelper._reload_completions(view)

    def reparse(self, view_id, verbose):
        if view_id in self.translation_units:
            if verbose:
                start = time.time()
                print(PKG_NAME + ": reparsing translation unit")
            self.translation_units[view_id].reparse()
            if verbose:
                print("{}: reparsed translation unit in {} sec".format(
                    PKG_NAME, time.time() - start))
            return True
        return False

    @staticmethod
    def _reload_completions(view):
        """Ask sublime to reload the completions. Needed to update the active 
        completion list when async autocompletion task has finished.

        Args:
            view (sublime.View): current_view

        """
        view.run_command('hide_auto_complete')
        view.run_command('auto_complete', {
            'disable_auto_insert': True,
            'api_completions_only': True,
            'next_competion_if_showing': True, })

    @staticmethod
    def _process_completions(complete_results):
        """Create snippet-like structures from a list of completions

        Args:
            complete_results (list): raw completions list

        Returns:
            list: updated completions
        """
        completions = []
        for c in complete_results.results:
            hint = ''
            contents = ''
            place_holders = 1
            for chunk in c.string:
                hint += chunk.spelling
                if chunk.isKindTypedText():
                    trigger = chunk.spelling
                if chunk.isKindResultType():
                    hint += ' '
                    continue
                if chunk.isKindOptional():
                    continue
                if chunk.isKindInformative():
                    continue
                if chunk.isKindPlaceHolder():
                    contents += ('${' + str(place_holders) + ':' +
                                 chunk.spelling + '}')
                    place_holders += 1
                else:
                    contents += chunk.spelling
            completions.append([trigger + "\t" + hint, contents])
        return completions

    @staticmethod
    def _search_clang_complete_file(start_folder, stop_folder):
        """search for .clang_complete file up the tree

        Args:
            start_folder (str): path to folder where we start the search
            stop_folder (str): path to folder we should not go beyond

        Returns:
            str: path to .clang_complete file or None if not found
        """
        current_folder = start_folder
        one_past_stop_folder = path.dirname(stop_folder)
        while current_folder != one_past_stop_folder:
            for file in listdir(current_folder):
                if file == ".clang_complete":
                    return path.join(current_folder, file)
            if (current_folder == path.dirname(current_folder)):
                break
            current_folder = path.dirname(current_folder)
        return None

    @staticmethod
    def _parse_clang_complete_file(file, verbose):
        """parse .clang_complete file

        Args:
            file (str): path to a file

        Returns:
            list(str): parsed list of includes from the file
        """
        includes = []
        folder = path.dirname(file)
        with open(file) as f:
            content = f.readlines()
            for line in content:
                if line.startswith("-I"):
                    path_to_add = line[2:].rstrip()
                    if path.isabs(path_to_add):
                        includes.append(path.normpath(path_to_add))
                    else:
                        includes.append(path.join(folder, path_to_add))
        if verbose:
            print("{}: .clang_complete contains includes: {}".format(
                PKG_NAME, includes))
        return includes
