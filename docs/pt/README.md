# KACE Studio

🌐 [English](../../README.md) · [Español](../../docs/es/README.md) · [Português](../../docs/pt/README.md)

![KACE Studio](../../web/KACE-studio-banner.png)

KACE Studio é o aplicativo Windows para preparar um Raspberry Pi com Klipper: imagem, primeira inicialização, descoberta, SSH e SFTP. Python controla validações e operações; JavaScript apresenta seus estados. [KACE](https://github.com/3D-uy/KACE/blob/main/docs/pt/README.md) no Pi controla a configuração da impressora e a instalação verificada.

**Candidato de teste controlado.** [release-contract.json](../../release-contract.json) define versão e insumos de compilação; [CHANGELOG](../../CHANGELOG.md) descreve o candidato atual. Alterações de código, validação do pacote, qualificação física e publicação assinada são estados separados. Um EXE anterior não inclui as alterações atuais do código.

**Distribuição sem assinatura.** Por decisão de produto, o KACE Studio será distribuído por enquanto sem assinatura Authenticode. Cada EXE distribuído deve manter seu SHA-256, manifesto de release, atestação do rebuild independente e commit exato de origem. A assinatura permanece como gate separado para uso futuro; sua ausência não bloqueia esta distribuição sem assinatura. Consulte o [checklist de release (EN)](../../RELEASE_CHECKLIST.md).

## Início rápido

Use Windows 10/11 com Microsoft Edge WebView2 Runtime. O desenvolvimento a partir do código contempla Python 3.11/3.12; pacotes exigem o toolchain exato do contrato de release. O writer solicita elevação para a operação de disco selecionada.

Execute a partir do código no Windows:

```powershell
git clone https://github.com/3D-uy/KACE-studio.git
cd KACE-studio
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --require-hashes -r requirements.lock
.\.venv\Scripts\python.exe main.py
```

## 🧭 Uso

1. **Smart Imager:** escolha modelo Pi, arquitetura, dashboard, origem da imagem e SD/USB exato.
2. **Credentials:** configure hostname, usuário, senha, rede e SSH. Revise o resumo antes de confirmar a gravação destrutiva.
3. Aguarde gravação, leitura de verificação/provisionamento e ejeção segura; depois inicie o Pi.
4. **Discovery → SSH Workspace:** selecione o Pi, verifique sua chave de host e conecte. Use o navegador SFTP para listar/baixar arquivos remotos; ele pertence à sessão SSH atual.
5. Inicie o bootstrap e continue KACE no Pi. Siga os passos manuais de firmware e aguarde a verificação do KACE; realize separadamente a preparação física da impressora.

## ⚠️ Escopo e limites

- As imagens oficiais e suas identidades vêm do [manifesto de imagens](../../image-manifest.json). Fluidd usa a base MainsailOS verificada mais bootstrap; não uma imagem arquivada do FluiddPI.
- Pi 5/500/500+/CM5 exigem 64-bit; Pi 4/400/CM4/CM4S, Pi 3/CM3 e Zero 2 W/CM2W oferecem os caminhos 32/64-bit suportados; Zero W usa 32-bit. Python valida a combinação selecionada.
- Imagens personalizadas exigem `.img` sem compressão e `.sha256`; as pre-baked personalizadas também exigem `.kace-attestation.json`. Não ignore verificações de checksum, identidade do disco ou capacidades.
- Listagens e downloads SFTP ficam vinculados à conexão de origem. Reconectar não prova sucesso da instalação; checkpoints pendentes exigem verificação do KACE.
- A autorização de cliente Moonraker salva explicitamente permissão para o IP deste computador após confirmação. Use somente quando precisar de acesso; um endereço obsoleto exige remoção manual.
- Testes automáticos e preview no navegador não comprovam mídia real, USB/MCU, elevação Windows nem WebView2 empacotado.

## 🛠️ Desenvolvimento

Consulte [Desenvolvimento (EN)](../../docs/DEVELOPMENT.md) para arquitetura, testes, frontend e limites entre código e pacote. Execute `python -m pytest -q` no ambiente configurado; harnesses frontend exigem Node. Validação de release e compilação seguem o checklist, não o preview no navegador.

## 📚 Documentação

| Necessidade | Guia |
| --- | --- |
| Desenvolvimento e testes (EN) | [DEVELOPMENT.md](../../docs/DEVELOPMENT.md) |
| Provisionamento e recuperação (EN) | [IMAGE_PROVISIONING.md](../../docs/IMAGE_PROVISIONING.md) |
| Release e qualificação de hardware (EN) | [RELEASE_CHECKLIST.md](../../RELEASE_CHECKLIST.md) |
| Roadmap | [ROADMAP.md](ROADMAP.md) |
| Notas do candidato atual | [CHANGELOG.md](../../CHANGELOG.md) |
| Segurança (EN) | [SECURITY.md](../../SECURITY.md) |

## Licença

[GPL-3.0](../../LICENSE).
