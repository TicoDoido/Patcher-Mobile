import os
import zlib
import threading
import flet as ft
from bsdiff_compat import diff as bsdiff_diff, patch as bsdiff_patch

CHUNK_SIZE = 2 * 1024 * 1024  # 2 MB (Mais seguro para Android)

def normalize_android_path(path):
    if not path:
        return path
    # Converte caminhos do Android SAF para caminhos de sistema de arquivos reais
    if "/document/primary:" in path:
        path = path.replace("/document/primary:", "/storage/emulated/0/")
    elif "/tree/primary:" in path:
        path = path.replace("/tree/primary:", "/storage/emulated/0/")
    
    # Alguns pickers podem retornar caminhos codificados
    path = path.replace("%3A", ":").replace("%2F", "/")
    
    return path

def create_patch(original_dir, modified_dir, patch_file, log_func, show_info, show_error):
    try:
        log_func(f"Iniciando criacao de patch...")
        log_func(f"Patch: {patch_file}")
        
        if not os.path.exists(original_dir):
            show_error("Erro", "Pasta Original nao existe!")
            return
        if not os.path.exists(modified_dir):
            show_error("Erro", "Pasta Modificada nao existe!")
            return
            
        modified_files = set()
        for root, _, files in os.walk(modified_dir):
            for file_name in files:
                relative_path = os.path.relpath(os.path.join(root, file_name), modified_dir)
                modified_files.add(relative_path)
        
        log_func(f"Arquivos na pasta modificada: {len(modified_files)}")
        diffs_found = 0

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
                                    log_func(f"  -> Diff detectado (P{chunk_idx}). Gerando delta...")
                                    delta = bsdiff_diff(o_chunk, m_chunk)
                                    compressed_delta = zlib.compress(delta, level=9)
                                    pf.write(len(relative_path).to_bytes(4, 'little'))
                                    pf.write(relative_path.encode('utf-8'))
                                    pf.write((2).to_bytes(1, 'little'))
                                    pf.write(chunk_idx.to_bytes(4, 'little'))
                                    pf.write(len(compressed_delta).to_bytes(4, 'little'))
                                    pf.write(compressed_delta)
                                    diffs_found += 1
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
                        pf.write(len(relative_path).to_bytes(4, 'little'))
                        pf.write(relative_path.encode('utf-8'))
                        pf.write((3).to_bytes(1, 'little'))
                        pf.write(chunk_idx.to_bytes(4, 'little'))
                        pf.write(len(compressed_data).to_bytes(4, 'little'))
                        pf.write(compressed_data)
                        log_func(f"  -> Adicionado (P{chunk_idx})")
                        diffs_found += 1
                        import gc
                        gc.collect()
                        chunk_idx += 1
        
        if diffs_found > 0:
            show_info("Sucesso", f"Patch criado com {diffs_found} alteracoes!")
        else:
            show_info("Aviso", "Nenhuma diferenca encontrada entre as pastas.")
    except Exception as e:
        show_error("Erro", f"Erro: {e}")

def apply_patch(original_dir, patch_file, log_func, show_info, show_error):
    try:
        log_func(f"Iniciando aplicacao de patch...")
        log_func(f"Arquivo: {patch_file}")
        
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
    page.title = "Patch Maker"
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
        help_text = (
            "Instrucoes de Uso:\n\n"
            "1. Permissoes (Android):\n"
            "- No Android 11+, a permissao principal e 'Acesso a todos os arquivos'.\n"
            "- Essa permissao normalmente NAO aparece na tela\n"
            "  Configuracoes > Apps > Permissoes.\n"
            "- Abra: Configuracoes > Apps > Acesso especial >\n"
            "  Acesso a todos os arquivos e ative o Patch Maker.\n"
            "- Em Android 10 ou inferior, o sistema pode pedir\n"
            "  permissao normal de armazenamento.\n\n"
            "2. Criar Patch:\n"
            "- Selecione a pasta original e a pasta modificada.\n"
            "- Escolha onde salvar o arquivo de patch.\n"
            "- Clique em 'Criar Patch' para gerar o delta.\n\n"
            "3. Aplicar Patch:\n"
            "- Selecione a pasta original e o arquivo de patch.\n"
            "- Clique em 'Aplicar Patch' para aplicar as mudancas.\n\n"
            "Nota: Faca backup dos seus arquivos antes de aplicar patches."
        )
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

    permission_handler = None

    def request_storage_permissions(_=None):
        if not permission_handler:
            log_func("Permissoes avancadas nao disponiveis neste build.")
            show_error(
                "Permissao indisponivel",
                "Este build nao possui PermissionHandler.\n"
                "No Android 11+, habilite manualmente em:\n"
                "Configuracoes > Apps > Acesso especial > Acesso a todos os arquivos.",
            )
            return

        # Em alguns builds, o controle pode existir mas ainda não estar anexado
        # de fato à página no momento do clique.
        if getattr(permission_handler, "page", None) is None:
            if permission_handler not in page.overlay:
                page.overlay.append(permission_handler)
            page.update()

        permission_names = [
            "MANAGE_EXTERNAL_STORAGE",  # Android 11+
            "READ_EXTERNAL_STORAGE",    # Android <= 12
            "WRITE_EXTERNAL_STORAGE",   # Android <= 10
        ]

        permissions = []
        permission_type_cls = getattr(ft, "PermissionType", None)
        if permission_type_cls:
            for name in permission_names:
                p = getattr(permission_type_cls, name, None)
                if p:
                    permissions.append(p)

        if not permissions:
            log_func("Tipos de permissao de armazenamento indisponiveis.")
            return

        try:
            request_permissions = getattr(permission_handler, "request_permissions", None)
            if callable(request_permissions):
                request_permissions(permissions)
            else:
                for permission in permissions:
                    permission_handler.request_permission(permission)
            log_func("Solicitacao de permissao enviada ao Android.")
            show_info(
                "Permissao de armazenamento",
                "Se nenhum popup aparecer no Android 11+, abra:\n"
                "Configuracoes > Apps > Acesso especial >\n"
                "Acesso a todos os arquivos e habilite o app.",
            )
        except Exception as e:
            log_func(f"Falha ao solicitar permissao: {e}")
            show_error("Erro", f"Nao foi possivel solicitar permissao: {e}")

    file_picker = ft.FilePicker()
    page.overlay.append(file_picker)
    selection_type = [""]

    def on_picker_result(e: ft.FilePickerResultEvent):
        res = e.path if e.path else (e.files[0].path if e.files else None)
        if res:
            res = normalize_android_path(res)
            if selection_type[0] == 'c_orig':  c_orig_field.value = res
            elif selection_type[0] == 'c_mod':  c_mod_field.value = res
            elif selection_type[0] == 'c_patch': c_patch_field.value = res
            elif selection_type[0] == 'a_orig':  a_orig_field.value = res
            elif selection_type[0] == 'a_patch': a_patch_field.value = res
            page.update()

    file_picker.on_result = on_picker_result

    # Campos - Criar Patch
    c_orig_field  = ft.TextField(label="Pasta Original",   expand=True)
    c_mod_field   = ft.TextField(label="Pasta Modificada", expand=True)
    c_patch_field = ft.TextField(label="Salvar Patch em", expand=True)

    # Campos - Aplicar Patch
    a_orig_field  = ft.TextField(label="Pasta Alvo",    expand=True)
    a_patch_field = ft.TextField(label="Arquivo Patch", expand=True)

    tabs = ft.Tabs(
        selected_index=0,
        animation_duration=300,
        tabs=[
            ft.Tab(
                text="Criar Patch",
                icon=ft.Icons.CREATE_NEW_FOLDER,
                content=ft.Container(
                    padding=20,
                    content=ft.Column([
                        ft.Row([ft.Text("Original:",  width=80), c_orig_field,  ft.ElevatedButton("...", on_click=lambda _: (selection_type.clear(), selection_type.append('c_orig'),  file_picker.get_directory_path()))]),
                        ft.Row([ft.Text("Modificada:", width=80), c_mod_field,  ft.ElevatedButton("...", on_click=lambda _: (selection_type.clear(), selection_type.append('c_mod'),   file_picker.get_directory_path()))]),
                        ft.Row([ft.Text("Patch:",      width=80), c_patch_field, ft.ElevatedButton("...", on_click=lambda _: (selection_type.clear(), selection_type.append('c_patch'), file_picker.save_file()))]),
                        ft.Row([
                            ft.ElevatedButton("Criar Patch", icon=ft.Icons.AUTO_FIX_HIGH, bgcolor=ft.Colors.GREEN_700, on_click=lambda _: threading.Thread(target=create_patch, args=(c_orig_field.value, c_mod_field.value, c_patch_field.value, log_func, show_info, show_error), daemon=True).start()),
                            ft.OutlinedButton("Solicitar Acesso", icon=ft.Icons.LOCK_OPEN, on_click=request_storage_permissions),
                        ], alignment="center"),
                    ], spacing=20)
                )
            ),
            ft.Tab(
                text="Aplicar Patch",
                icon=ft.Icons.SYSTEM_UPDATE_ALT,
                content=ft.Container(
                    padding=20,
                    content=ft.Column([
                        ft.Row([ft.Text("Pasta:",      width=80), a_orig_field,  ft.ElevatedButton("...", on_click=lambda _: (selection_type.clear(), selection_type.append('a_orig'),  file_picker.get_directory_path()))]),
                        ft.Row([ft.Text("Patch:",      width=80), a_patch_field, ft.ElevatedButton("...", on_click=lambda _: (selection_type.clear(), selection_type.append('a_patch'), file_picker.pick_files()))]),
                        ft.Row([
                            ft.ElevatedButton("Aplicar Patch", icon=ft.Icons.PLAY_ARROW, bgcolor=ft.Colors.BLUE_700, on_click=lambda _: threading.Thread(target=apply_patch, args=(a_orig_field.value, a_patch_field.value, log_func, show_info, show_error), daemon=True).start()),
                        ], alignment="center"),
                    ], spacing=20)
                )
            ),
        ],
        expand=True
    )

    page.add(
        ft.Column([
            ft.Row([
                ft.Text("Patch Maker", size=24, weight="bold"),
                ft.Row([
                    ft.IconButton(ft.Icons.HELP_OUTLINE, on_click=show_help),
                    ft.IconButton(ft.Icons.DELETE_SWEEP, tooltip="Limpar Log", on_click=clear_log)
                ])
            ], alignment="spaceBetween"),
            tabs,
            ft.Divider(),
            ft.Text("Relatorio:", size=14, weight="bold"),
            ft.Container(
                content=log_list,
                height=150,
                border_radius=5,
                border=ft.border.all(1, ft.Colors.BLUE_GREY_700),
                padding=5,
            )
        ], expand=True)
    )

    # Alguns builds/plataformas não incluem o controle PermissionHandler.
    # Evita o erro "unknown control: permission_handler" e mantém o app funcional.
    permission_handler_cls = getattr(ft, "PermissionHandler", None)

    if permission_handler_cls:
        try:
            permission_handler = permission_handler_cls()
            page.overlay.append(permission_handler)
            page.update()
            request_storage_permissions()
        except Exception as e:
            print(f"Permissão avançada indisponível nesta plataforma/build: {e}")

ft.app(main)
