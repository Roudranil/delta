# Tracking secrets with git

This repo tracks API keys and secrets using [`git-crypt`](https://github.com/AGWA/git-crypt). `git-crypt` encrypts the secrets file with a secure key stored on the development machine. The files to be encrypted by `git-crypt` are mentioned in [.gitattributes](../.gitattributes).

When committed to GitHub, the files are stored in encrypted binary and cannot be decrypted without the key. However, the person who possesses the key can decrypt them and view history.