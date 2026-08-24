"""Cria o primeiro usuario administrador. Rode uma vez antes de usar o site:

    python setup_admin.py

A senha e digitada de forma oculta (nao aparece na tela nem fica salva em
nenhum log) e e guardada com hash (nunca em texto puro).
"""
import getpass
import sys

import models


def main():
    models.init_db()
    print("=== Criar usuario administrador do BioScout Web ===")
    username = input("Usuario (ex.: seu nome ou e-mail): ").strip()
    if not username:
        print("Usuario nao pode ser vazio.")
        sys.exit(1)
    if models.get_user_by_username(username):
        print(f"Ja existe um usuario '{username}'. Rode o site e altere a senha por la, ou escolha outro nome.")
        sys.exit(1)

    password = getpass.getpass("Senha: ")
    password2 = getpass.getpass("Confirme a senha: ")
    if not password:
        print("Senha nao pode ser vazia.")
        sys.exit(1)
    if password != password2:
        print("As senhas nao conferem.")
        sys.exit(1)

    models.create_user(username, password, is_admin=True)
    print(f"\nUsuario administrador '{username}' criado com sucesso.")
    print("Agora rode: python app.py  e acesse http://localhost:5000")


if __name__ == "__main__":
    main()
