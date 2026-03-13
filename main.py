import os
import zlib
import threading
import flet as ft
from bsdiff_compat import diff as bsdiff_diff, patch as bsdiff_patch

CHUNK_SIZE = 1024 * 1024 * 1024  # 1 GB

def create_patch(original_dir, modified_dir, patch_file, log_func, show_info, show_error):
    try:
        modified_files = set()
        for root, _, files in os.walk(modified_dir):
            for file_name in files:
                relative_path = os.path.relpath(os.path.join(root, file_name), modified_dir)
                modified_files.add(relative_path)

        with open(patch_file, 'wb') as pf:
            for root, _, files in os.walk(original_dir):
                for file_name in files:
                    original_path = os.path.join(root, file_name)
                    relative_path = os.path.relpath(original_path, original_dir)
                    modified_path = os.path.join(modified_dir, relative_path)

                    if os.path.exists(modified_path):
                        log_func(f"Analisando: {relative_path}")
                        with open(original_path, 'rb') as f1, open(modified_path, 'rb') as f2:
                            chunk_idx = 0
                            while True:
                                o_chunk = f1.read(CHUNK_SIZE)
                                m_chunk = f2.read(CHUNK_SIZE)
                                if not o_chunk and not m_chunk:
                                    break
                                o_chunk = o_chunk or b""
                                m_chunk = m_chunk or b""
                                if o_chunk != m_chunk:
                                    log_func(f"  -> Diferenca na Parte {chunk_idx}. Gerando delta...")
                                    delta = bsdiff_diff(o_chunk, m_chunk)
                                    del o_chunk, m_chunk
                                    compressed_delta = zlib.compress(delta, level=9)
                                    del delta
                                    pf.write(len(relative_path).to_bytes(4, 'little'))
                                    pf.write(relative_path.encode('utf-8'))
                                    pf.write((2).to_bytes(1, 'little'))
                                    pf.write(chunk_idx.to_bytes(4, 'little'))
                                    pf.write(len(compressed_delta).to_bytes(4, 'little'))
                                    pf.write(compressed_delta)
                                    del compressed_delta
                                else:
                                    del o_chunk, m_chunk
                                chunk_idx += 1
                                import gc
                                gc.collect()
                        modified_files.discard(relative_path)

            for relative_path in modified_files:
                modified_path = os.path.join(modified_dir, relative_path)
                log_func(f"Novo arquivo: {relative_path}")
                with open(modified_path, 'rb') as f:
                    chunk_idx = 0
                    while True:
                        data = f.read(CHUNK_SIZE)
                        if not data:
                            break
                        compressed_data = zlib.compress(data, level=9)
                        del data
                        pf.write(len(relative_path).to_bytes(4, 'little'))
                        pf.write(relative_path.encode('utf-8'))
                        pf.write((3).to_bytes(1, 'little'))
                        pf.write(chunk_idx.to_bytes(4, 'little'))
                        pf.write(len(compressed_data).to_bytes(4, 'little'))
                        pf.write(compressed_data)
                        log_func(f"  -> Parte {chunk_idx} adicionada")
                        del compressed_data
                        import gc
                        gc.collect()
                        chunk_idx += 1
        show_info("Sucesso", "Patch criado com sucesso!")
    except Exception as e:
        show_error("Erro", f"Erro: {e}")

def apply_patch(original_dir, patch_file, log_func, show_info, show_error):
    try:
        if not os.path.exists(patch_file):
            show_error("Erro", "Patch nao encontrado!")
            return
        with open(patch_file, 'rb') as pf:
            while True:
                path_size_bytes = pf.read(4)
                if not path_size_bytes:
                    break
                path_size = int.from_bytes(path_size_bytes, 'little')
                relative_path = pf.read(path_size).decode('utf-8')
                patch_type = int.from_bytes(pf.read(1), 'little')
                output_path = os.path.join(original_dir, relative_path)
                os.makedirs(os.path.dirname(output_path), exist_ok=True)
                chunk_idx = int.from_bytes(pf.read(4), 'little')
                data_size = int.from_bytes(pf.read(4), 'little')
                compressed_data = pf.read(data_size)
                data = zlib.decompress(compressed_data)
                del compressed_data

                if patch_type == 2:
                    mode = 'r+b' if os.path.exists(output_path) else 'wb'
                    with open(output_path, mode) as f:
                        f.seek(chunk_idx * CHUNK_SIZE)
                        o_chunk = f.read(CHUNK_SIZE) or b""
                        m_chunk = bsdiff_patch(o_chunk, data)
                        f.seek(chunk_idx * CHUNK_SIZE)
                        f.write(m_chunk)
                    import gc
                    del o_chunk, m_chunk, data
                    gc.collect()
                    log_func(f"Atualizado: {relative_path} (P{chunk_idx})")
                elif patch_type == 3:
                    mode = 'r+b' if os.path.exists(output_path) else 'wb'
                    with open(output_path, mode) as f:
                        f.seek(chunk_idx * CHUNK_SIZE)
                        f.write(data)
                    import gc
                    del data
                    gc.collect()
                    log_func(f"Criado: {relative_path} (P{chunk_idx})")
        show_info("Sucesso", "Patch aplicado!")
    except Exception as e:
        show_error("Erro", f"Erro: {e}")

def main(page: ft.Page):
    page.title = "Gerenciador de Patches v2 - TicoDoido"
    page.window.width = 680
    page.window.height = 650
    page.theme_mode = ft.ThemeMode.DARK

    def show_info(title, message):
        dlg = ft.AlertDialog(title=ft.Text(title), content=ft.Text(message))
        page.open(dlg)

    def show_error(title, message):
        dlg = ft.AlertDialog(title=ft.Text(title), content=ft.Text(message))
        page.open(dlg)

    def show_help(_):
        help_text = "Instrucoes de Uso:\n\n1. Criar Patch:\n- Selecione a pasta original e a pasta modificada.\n- Escolha onde salvar o arquivo de patch.\n- Clique em 'Criar Patch' para gerar um \n    arquivo de patch dos arquivos modificados.\n\n2. Aplicar Patch:\n- Selecione a pasta original e o arquivo de patch.\n    E NORMAL APARECER QUE O ARQUIVO DE PATCH \n    JA EXISTE AO SELECIONAR ELE NA APLICACAO \n    DO PATCH, E SO RESPONDER SIM !!!\n- Clique em 'Aplicar Patch' para aplicar \n    as modificacoes na pasta original.\n\nNota: Certifique-se de fazer backup da pasta original antes de aplicar o patch."
        dlg = ft.AlertDialog(
            title=ft.Text("Ajuda"),
            content=ft.Text(help_text),
            actions=[ft.TextButton("Fechar", on_click=lambda e: page.close(dlg))]
        )
        page.open(dlg)

    log_list = ft.ListView(expand=True, spacing=5, auto_scroll=True)

    def log_func(message: str):
        log_list.controls.append(ft.Text(message, size=12, color=ft.Colors.GREEN_400))
        if len(log_list.controls) > 7:
            del log_list.controls[0]
        page.update()

    def clear_log(_):
        log_list.controls.clear()
        log_func("Log limpo!")

    file_picker = ft.FilePicker()
    page.overlay.append(file_picker)
    selection_type = [""]

    def on_picker_result(e: ft.FilePickerResultEvent):
        res = e.path if e.path else (e.files[0].path if e.files else None)
        if res:
            if selection_type[0] == 'orig': orig_field.value = res
            elif selection_type[0] == 'mod': mod_field.value = res
            elif selection_type[0] == 'patch': patch_field.value = res
            page.update()

    file_picker.on_result = on_picker_result

    orig_field  = ft.TextField(label="Pasta Original",   expand=True)
    mod_field   = ft.TextField(label="Pasta Modificada", expand=True)
    patch_field = ft.TextField(label="Arquivo de Patch", expand=True)

    page.add(
        ft.Container(
            padding=15,
            content=ft.Column([
                ft.Row([ft.Text("Original:",  width=80), orig_field,  ft.ElevatedButton("...", on_click=lambda _: (selection_type.clear(), selection_type.append('orig'),  file_picker.get_directory_path()))]),
                ft.Row([ft.Text("Modificada:", width=80), mod_field,  ft.ElevatedButton("...", on_click=lambda _: (selection_type.clear(), selection_type.append('mod'),   file_picker.get_directory_path()))]),
                ft.Row([ft.Text("Patch:",      width=80), patch_field, ft.ElevatedButton("...", on_click=lambda _: (selection_type.clear(), selection_type.append('patch'), file_picker.save_file()))]),

                ft.Row([
                    ft.ElevatedButton("Criar Patch",   bgcolor=ft.Colors.GREEN_700, on_click=lambda _: threading.Thread(target=create_patch, args=(orig_field.value, mod_field.value, patch_field.value, log_func, show_info, show_error), daemon=True).start()),
                    ft.ElevatedButton("Aplicar Patch", bgcolor=ft.Colors.BLUE_700,  on_click=lambda _: threading.Thread(target=apply_patch,  args=(orig_field.value, patch_field.value, log_func, show_info, show_error), daemon=True).start()),
                    ft.ElevatedButton("Ajuda", on_click=show_help),
                    ft.IconButton(ft.Icons.DELETE_SWEEP, tooltip="Limpar Log", on_click=clear_log)
                ], alignment="center"),

                ft.Column([
                    ft.Row([ft.Text("Relatorio:", size=14, weight="bold")]),
                    ft.Container(
                        content=log_list,
                        expand=True,
                        border_radius=5,
                        border=ft.border.all(1, ft.Colors.BLUE_GREY_700),
                        padding=5,
                    )
                ], expand=True)
            ], expand=True)
        )
    )

ft.app(main)