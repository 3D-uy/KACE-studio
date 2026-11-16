# KACE Studio — Roadmap

🌐 [English](../../ROADMAP.md) · [Español](../../docs/es/ROADMAP.md) · [Português](../../docs/pt/ROADMAP.md)

Prioridades deste código, sem datas prometidas ou afirmações de qualificação concluída. Os guias de release continuam sendo a autoridade dos gates; este roadmap não altera versões, hashes, targets de firmware nem pins.

## 📍 Disponível no código

Existem imagem guiada, primeira inicialização, Discovery, SSH/SFTP, progresso do bootstrap e recuperação do checkpoint KACE. Python mantém a autoridade; o frontend deve respeitar validações, identidades de operação e resultados terminais.

As correções no código acrescentam busca remota explícita de operações após reabrir, publicação SFTP ligada à identidade, requisições Moonraker limitadas e desligamento normal revisado com ação separada de parada de emergência Klipper. Reconexão nunca retoma automaticamente; parada de emergência não é isolamento elétrico. A autorização persistente de IP está alinhada em EN/ES/PT. Essas alterações locais não qualificam WebView2 nativo, o EXE histórico, primeiro boot nem comportamento físico da energia. Faltam gates de runner limpo, pacote imutável e E2E manuais.

## 🧭 Prioridades

A ajuda do Crowsnest esclarece que desmarcar a opção preserva uma instalação existente. Verificar essa explicação em EN/ES/PT e WebView2 nativo, e testar uma imagem limpa e outra com Crowsnest presente durante a qualificação controlada do primeiro boot; esta alteração de código não qualifica um pacote.

| Prioridade | Resultado exigido | Referência |
| --- | --- | --- |
| 1 · Validação da interface | Verificar seletores/ícones do Imager, teclado e foco, credenciais, SFTP persistente e recuperação tanto no código quanto no WebView2 nativo. | [Desenvolvimento (EN)](../../docs/DEVELOPMENT.md) |
| 2 · Evidência de código e pacote | Conciliar a matriz suportada de SO/Python; validar depois bootstrap de código/empacotado, assets web exatos e smoke do renderer empacotado. | [Checklist (EN)](../../RELEASE_CHECKLIST.md) |
| 3 · Qualificação controlada | Testar identidade real do destino, elevação, leitura de verificação, ejeção, primeira inicialização, SSH/SFTP e conclusão KACE sob controle do operador. | [Provisionamento (EN)](../../docs/IMAGE_PROVISIONING.md) |
| 4 · Distribuição e manutenção | Distribuir o candidato atual sem Authenticode, mantendo SHA-256, manifesto de release, atestação do rebuild independente e commit exato de origem. Adiar a assinatura para uma release assinada futura, preservando seu gate. Manter paridade de idiomas e evidência de regressão. | [Checklist (EN)](../../RELEASE_CHECKLIST.md) |

## Limites de escopo

Testes no Linux não equivalem a suporte ao desktop/writer no Linux. Novos targets de firmware e decisões de segurança da impressora pertencem ao KACE. Plataformas ou fluxos adicionais exigem escopo e validação separados.

[Voltar ao README](README.md)

As ações oficiais de retomada e configuração agora recusam importar ou executar KACE durante uma atualização do runtime ou enquanto seu journal continuar pendente. Após uma atualização interrompida, concluir a recuperação com o instalador verificado antes de retomar. Essa proteção no código não qualifica o executável histórico.
