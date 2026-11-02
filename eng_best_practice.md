- DO NOT overengineer!! If existing code can be easily and significatly simplified without downgrade - simplify.
- However, do not oversimplify - shortcuts are not allowed. 
- Can install ANY geometry packages if they make code shorter and more reliable. However, take advantage of low dimensions of images if it's possible, multi-views - it medical images it could be a game changer.
- Write code that is compact and easy to refactor, yet meet all requirements as by plan. 
- Use exiting python packages if needed. If cannot install, show requirements, I will update env.
- Use pytest wisely - create a tiney subset of dataset to quickly validate logic; execute the bigger runs only when all is working.
- The problem is very difficult - DO NOT try to solve it perfectly, i.e. 90% success rate is excellent. However, architecture must imply that failures will be solved via mechanical extension of code (f.e. via extra patterns, etc) 
- For each stage/complex changes create a git fork, ask user to merge
- Do NOT run test on all worlds - use a reasonable subset (usually 10-20% is statistically enough), or 1-2 subsets for high CI, save tokens on non-coding tasks - design concise debug output, etc

