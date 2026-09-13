# Short-form scoring rubric

Score to one decimal place. These scores prioritize what to record; they do not forecast views.

## Virality /10

Judge the whole concept:

- 25% audience breadth and urgency;
- 20% novelty or contrarian tension;
- 20% emotional or economic stakes;
- 20% shareability and identity value;
- 15% visible credibility or proof.

`9–10`: broad consequential desire, sharp angle, concrete stakes, and strong proof.

`5–6`: useful but familiar, narrower, or weakly evidenced.

`1–2`: unclear viewer, commodity advice, or no reason to share.

## Three-second hook /10

Judge only the opening:

- 30% immediate clarity;
- 25% specificity;
- 20% tension or curiosity;
- 15% credibility;
- 10% payoff alignment.

`9–10`: instantly understood, specific, consequential, credible, and directly repayable.

`5–6`: understandable but familiar, wordy, or missing stakes.

`1–2`: vague, confusing, unsupported, or disconnected from the body.

## Payoff confidence

Keep `payoff_confidence` as an internal 1–10 score for ranking. It measures whether the available proof and three bullets can fully resolve the hook. Do not display it unless requested.

Calculate internal priority as:

`priority = virality*0.40 + three_second_hook*0.40 + payoff_confidence*0.20`
