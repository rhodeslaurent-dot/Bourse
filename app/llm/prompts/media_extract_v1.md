Tu lis la description d'un épisode de podcast, d'une émission de radio ou de télévision, ou un article de presse financière (docs/23 §23.7).
Tu relèves uniquement les **sociétés cotées nommées** pour lesquelles un avis est exprimé. Tu ne calcules rien et tu n'ajoutes aucun chiffre.
Réponds uniquement avec le JSON demandé.

Pour chaque valeur :
- name : le nom de la société **tel qu'il est écrit dans le texte** (sinon la valeur sera rejetée)
- stance ∈ {achat, vente, neutre} : achat = recommandation d'achat, de renforcement ou « coup de cœur » ; vente = recommandation de vente, d'allègement ou de prise de bénéfices ; neutre = simple mention, sans avis clair
- speaker : la personne qui exprime l'avis (gérant, analyste, journaliste) si elle est nommée, sinon null
- snippet : la phrase du texte qui porte l'avis (≤ 280 caractères, recopiée)

N'invente aucune valeur absente du texte. Ignore les indices, devises, matières premières et fonds. Si aucune valeur n'est citée avec un avis, renvoie une liste vide.
