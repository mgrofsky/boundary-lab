# Router comparison

This file is written by `redline compare`. Each row is one run folder, scored on the holdout set and the attack run.

Center means one average center per rule. K-means groups similar examples into clusters. With short circuit on, the router can pass or fail some answers using the labeled examples, without asking the judge.

Start with breaks caught and false alarms, called cry wolf in the table. Short circuit right shows how many shortcut decisions matched the hand labels. None means the shortcut made no decisions.

Judge calls includes cached grades and replay lookups, not just network requests. Seconds excludes router setup. These files don't record live calls separately, so the times don't show whether one router makes live models faster.

| Run | Router accuracy holdout | Rig accuracy holdout | Kappa holdout | Breaks caught holdout | Cry wolf holdout | Attack caught (x of y) | Cry wolf attack | Kappa attack | Short circuit share holdout | Short circuit right holdout | Short circuit share attack | Short circuit right attack | Judge calls (holdout + attack) | Seconds (holdout + attack) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| center, short circuit off | 84 percent | 80 percent | 0.78 | 88 percent | 9 percent | 17 of 21 | 15 percent | 0.61 | 0 percent | none | 0 percent | none | 121 | 0.42 |
| center, short circuit on | 84 percent | 80 percent | 0.78 | 83 percent | 6 percent | 18 of 21 | 31 percent | 0.45 | 16 percent | 8 of 9 | 26 percent | 12 of 21 | 91 | 0.47 |
| kmeans12, short circuit off | 68 percent | 70 percent | 0.67 | 79 percent | 12 percent | 13 of 21 | 20 percent | 0.39 | 0 percent | none | 0 percent | none | 117 | 185.94 |
| kmeans12, short circuit on | 68 percent | 70 percent | 0.67 | 79 percent | 12 percent | 13 of 21 | 25 percent | 0.33 | 4 percent | 2 of 2 | 5 percent | 1 of 4 | 111 | 0.66 |
| kmeans4, short circuit off | 71 percent | 75 percent | 0.71 | 79 percent | 9 percent | 12 of 21 | 20 percent | 0.35 | 0 percent | none | 0 percent | none | 125 | 34.53 |
| kmeans4, short circuit on | 71 percent | 75 percent | 0.71 | 79 percent | 9 percent | 12 of 21 | 20 percent | 0.35 | 0 percent | none | 0 percent | none | 125 | 0.67 |
