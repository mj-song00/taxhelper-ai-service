package taxhelper.chatservice.domain.precedent.service;

import lombok.Setter;
import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.stereotype.Component;

import java.util.ArrayList;
import java.util.List;

@Component
@ConfigurationProperties(prefix = "precedent")
@Setter
public class PrecedentProperties {

    private List<String> keywords = new ArrayList<>();;

    public List<String> getKeywords() {
        return keywords;
    }
}
